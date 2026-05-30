"""Per-frame "human" modulation for the offline auto-editor.

Unlike the realtime ``modulation.ModulationEngine`` (which runs in a 60 Hz
thread off the live microphone), this matrix is *render-time* and *stateless*:
for every exported frame it is handed the absolute timeline position ``t`` and
the pre-computed :class:`automatch.AudioAnalysis`, and it nudges the per-frame
``Params`` snapshot just before the frame is rendered.

Design goals (the "human" part):
  - internal LFOs (sine / triangle / saw / square / random / sample-&-hold),
    free-running in Hz *or* synced to the beat grid (cycles-per-beat), with an
    optional phase reset on every downbeat so the wobble locks to the bar;
  - musical envelopes derived from the analysis — onset ``energy``, ``rms``
    loudness, beat / downbeat pulse envelopes, and ``drop`` detection;
  - a small modulation matrix where several routes may target the same
    parameter and be combined (add / multiply / average), modulating either
    *around* the parameter's base value (``bipolar``) or onto an absolute
    ``lo..hi`` range (``absolute``).

Everything is opt-in: an empty ``mod_config`` makes :meth:`ModMatrix.apply` a
no-op, so the default render is byte-for-byte unchanged.

``mod_config`` shape (JSON-serialisable, stored on ``AutoEditSettings``)::

    {
      "lfos": [
        {"name": "lfo1", "wave": "sine", "rate": 0.25,
         "beat_sync": true, "phase": 0.0,
         "reset_on_downbeat": true, "seed": 0},
        ...
      ],
      "routes": [
        {"target": "distort_scale", "source": "lfo1",
         "mode": "bipolar", "depth": 0.15, "curve": 1.0,
         "lo": 0.0, "hi": 1.0, "invert": false, "combine": "add"},
        {"target": "effect_mix_glitch", "source": "beat",
         "mode": "bipolar", "depth": 0.4, ...},
        ...
      ]
    }
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ── source / mode vocabularies (mirrors modulation.py style) ────────────────
LFO_WAVES = ("sine", "triangle", "saw", "square", "random", "sh")
AUDIO_MOD_SOURCES = ("energy", "rms", "beat", "downbeat", "drop")
LFO_NAMES = ("lfo1", "lfo2", "lfo3", "lfo4")
MOD_SOURCES = ("off",) + AUDIO_MOD_SOURCES + LFO_NAMES
COMBINE_MODES = ("add", "mul", "avg")
MOD_MODES = ("bipolar", "absolute")


# ── small deterministic helpers ─────────────────────────────────────────────
def _hash01(n: int, seed: int = 0) -> float:
    """Deterministic pseudo-random float in [0,1) from an integer cycle index."""
    x = (int(n) * 374761393 + int(seed) * 668265263) & 0xFFFFFFFF
    x = (x ^ (x >> 13)) * 1274126177 & 0xFFFFFFFF
    x = x ^ (x >> 16)
    return (x & 0xFFFFFFFF) / 4294967296.0


def _last_le(times, t: float) -> float:
    """Largest value in the (sorted) array ``times`` that is <= t, else -1."""
    last = -1.0
    for b in times:
        bf = float(b)
        if bf <= t:
            last = bf
        else:
            break
    return last


def _beat_env(t: float, times, decay: float = 0.18) -> float:
    """Stateless beat-triggered decay envelope: 1.0 at each beat, exp-decaying."""
    lb = _last_le(times, t)
    if lb < 0.0:
        return 0.0
    dt = t - lb
    if dt < 0.0:
        return 0.0
    return math.exp(-dt / max(1e-3, decay))


# ── LFO ─────────────────────────────────────────────────────────────────────
@dataclass
class Lfo:
    wave: str = "sine"
    rate: float = 1.0            # Hz, or cycles-per-beat when beat_sync
    beat_sync: bool = False
    phase: float = 0.0           # 0..1 starting offset
    reset_on_downbeat: bool = False
    seed: int = 0

    def cycles(self, t: float, bpm: float, last_downbeat_t: float) -> float:
        base_t = t
        if self.reset_on_downbeat and last_downbeat_t >= 0.0:
            base_t = max(0.0, t - last_downbeat_t)
        if self.beat_sync:
            beat_dur = 60.0 / max(1e-3, bpm)
            c = (base_t / beat_dur) * self.rate
        else:
            c = base_t * self.rate
        return c + self.phase

    def value(self, cycles: float) -> float:
        """Bipolar output in [-1, 1]."""
        p = cycles - math.floor(cycles)          # phase 0..1
        w = self.wave
        if w == "sine":
            return math.sin(2.0 * math.pi * p)
        if w == "saw":
            return 2.0 * p - 1.0
        if w == "square":
            return 1.0 if p < 0.5 else -1.0
        if w == "triangle":
            return 4.0 * abs(p - 0.5) - 1.0      # peak at p=0, trough at p=0.5
        if w == "sh":                            # sample & hold (stepped)
            return _hash01(math.floor(cycles), self.seed) * 2.0 - 1.0
        if w == "random":                        # smooth random (interpolated)
            i = math.floor(cycles)
            a = _hash01(i, self.seed)
            b = _hash01(i + 1, self.seed)
            f = cycles - i
            f = f * f * (3.0 - 2.0 * f)          # smoothstep
            return (a + (b - a) * f) * 2.0 - 1.0
        return 0.0


# ── route ────────────────────────────────────────────────────────────────────
@dataclass
class AutoModRoute:
    target: str                  # Params field name, or "effect_mix_{kind}"
    source: str = "off"          # one of MOD_SOURCES
    mode: str = "bipolar"        # "bipolar" (around base) | "absolute" (lo..hi)
    depth: float = 0.2           # bipolar: param-units of swing
    curve: float = 1.0           # exponent on the (unipolar) source
    lo: float = 0.0              # absolute range / clamp lower bound
    hi: float = 1.0              # absolute range / clamp upper bound
    invert: bool = False
    combine: str = "add"         # add | mul | avg (per-target, first route wins)


# ── parameter get/set (mirrors state.apply_modulated_params semantics) ───────
def _get_param(params, target: str):
    if target.startswith("effect_mix_"):
        kind = target[len("effect_mix_"):]
        for eff in getattr(params, "effects", []):
            if eff.kind == kind:
                return float(eff.mix)
        return None
    if hasattr(params, target):
        v = getattr(params, target)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _set_param(params, target: str, value: float) -> None:
    if target.startswith("effect_mix_"):
        kind = target[len("effect_mix_"):]
        for eff in getattr(params, "effects", []):
            if eff.kind == kind:
                eff.mix = float(value)
                return
        return
    if hasattr(params, target):
        setattr(params, target, float(value))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── matrix ────────────────────────────────────────────────────────────────────
class ModMatrix:
    """Parses a ``mod_config`` dict and applies it per frame."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.lfos: dict[str, Lfo] = {}
        for i, ld in enumerate(config.get("lfos", []) or []):
            name = ld.get("name") or f"lfo{i + 1}"
            self.lfos[name] = Lfo(
                wave=str(ld.get("wave", "sine")),
                rate=float(ld.get("rate", 1.0)),
                beat_sync=bool(ld.get("beat_sync", False)),
                phase=float(ld.get("phase", 0.0)),
                reset_on_downbeat=bool(ld.get("reset_on_downbeat", False)),
                seed=int(ld.get("seed", 0)),
            )
        self.routes: list[AutoModRoute] = []
        for rd in config.get("routes", []) or []:
            tgt = rd.get("target")
            if not tgt:
                continue
            self.routes.append(AutoModRoute(
                target=str(tgt),
                source=str(rd.get("source", "off")),
                mode=str(rd.get("mode", "bipolar")),
                depth=float(rd.get("depth", 0.2)),
                curve=float(rd.get("curve", 1.0)),
                lo=float(rd.get("lo", 0.0)),
                hi=float(rd.get("hi", 1.0)),
                invert=bool(rd.get("invert", False)),
                combine=str(rd.get("combine", "add")),
            ))

    # convenience: is this matrix going to do anything at all?
    @property
    def active(self) -> bool:
        return any(r.source != "off" for r in self.routes)

    def build_sources(self, t: float, audio) -> dict[str, float]:
        """Evaluate every source at timeline position ``t``."""
        bpm = float(getattr(audio, "bpm", 120.0) or 120.0)
        beats = getattr(audio, "beats", [])
        downbeats = getattr(audio, "downbeats", [])
        src: dict[str, float] = {
            "energy": float(audio.energy_at(t)),
            "rms": float(audio.rms_at(t)),
            "beat": _beat_env(t, beats),
            "downbeat": _beat_env(t, downbeats),
            "drop": 1.0 if audio.is_drop(t) else 0.0,
        }
        last_db = _last_le(downbeats, t)
        for name, lfo in self.lfos.items():
            src[name] = lfo.value(lfo.cycles(t, bpm, last_db))
        return src

    def apply(self, params, t: float, audio) -> None:
        """Mutate ``params`` in place. No-op when there are no live routes."""
        if not self.active:
            return
        sources = self.build_sources(t, audio)

        # group routes by target so multiple sources can combine
        groups: dict[str, list[AutoModRoute]] = {}
        for r in self.routes:
            if r.source == "off" or r.source not in sources:
                continue
            groups.setdefault(r.target, []).append(r)

        for target, rs in groups.items():
            base = _get_param(params, target)
            if base is None:
                continue

            vals: list[float] = []
            for r in rs:
                s = sources[r.source]
                is_lfo = r.source in LFO_NAMES
                if r.mode == "absolute":
                    u = (s + 1.0) * 0.5 if is_lfo else _clamp(s, 0.0, 1.0)
                    u = math.pow(_clamp(u, 0.0, 1.0), max(0.05, r.curve))
                    if r.invert:
                        u = 1.0 - u
                    vals.append(r.lo + (r.hi - r.lo) * u)
                else:  # bipolar — modulate around the base value
                    if is_lfo:
                        sig = s                      # already [-1, 1]
                    else:
                        sig = math.pow(_clamp(s, 0.0, 1.0), max(0.05, r.curve))
                    if r.invert:
                        sig = -sig
                    vals.append(base + sig * r.depth)

            combine = rs[0].combine
            if combine == "avg":
                out = sum(vals) / len(vals)
            elif combine == "mul":
                out = base
                for v in vals:
                    out *= (v / base) if abs(base) > 1e-9 else 1.0
            else:  # add — sum of deltas from base
                out = base + sum(v - base for v in vals)

            lo = min(min(r.lo, r.hi) for r in rs)
            hi = max(max(r.lo, r.hi) for r in rs)
            _set_param(params, target, _clamp(out, lo, hi))


# ── ready-made presets ────────────────────────────────────────────────────────

def _beat_spike_routes(source: str) -> list[dict]:
    """Build 6 effect-mix routes for the given beat source."""
    effects = ("vhs", "rgb_split", "wave_warp", "pixel_sort", "datamosh", "distort")
    return [
        {
            "target": f"effect_mix_{fx}",
            "source": source,
            "mode": "absolute",
            "depth": 1.0,
            "curve": 1.0,
            "lo": 0.3,
            "hi": 0.85,
            "invert": False,
            "combine": "add",
        }
        for fx in effects
    ]


BEAT_SPIKE_PRESET: dict = {
    "lfos": [
        {
            "name": "lfo1",
            "wave": "sine",
            "rate": 0.5,
            "beat_sync": True,
            "phase": 0.0,
            "reset_on_downbeat": False,
            "seed": 0,
        }
    ],
    "routes": _beat_spike_routes("beat"),
}
"""Ритмический спайк на каждый бит.

Все шесть effect_mix скачут до 0.85 на ударе барабана и экспоненциально
возвращаются к 0.3 между ударами. lfo1 (синус, 0.5 цикла/бит) доступен
как дополнительный источник, если маршруты добавляются вручную.
"""

DOWNBEAT_PULSE_PRESET: dict = {
    "lfos": [
        {
            "name": "lfo1",
            "wave": "sine",
            "rate": 0.25,
            "beat_sync": True,
            "phase": 0.0,
            "reset_on_downbeat": True,
            "seed": 0,
        }
    ],
    "routes": _beat_spike_routes("downbeat"),
}
"""Тяжёлый пульс на каждый даунбит (раз в такт).

Аналогичен BEAT_SPIKE_PRESET, но source="downbeat" — срабатывает реже,
создаёт акцент на первой доле каждого такта. lfo1 сбрасывается на каждом
даунбите (reset_on_downbeat=True) и делает 0.25 цикла/бит.
"""
