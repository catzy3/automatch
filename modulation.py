"""Audio-reactive modulation engine — Resolume-Arena-style routes.

Each ModRoute maps one audio band to one parameter, shaping the response with:
  - gain          — amplifies the raw signal before everything else
  - threshold     — gate; signal below this is treated as 0
  - curve         — exponent applied to gated signal (0.25..4.0)
                    1.0 = linear, <1 = aggressive (responds to soft input),
                    >1 = squashed (only loud sounds register)
  - range_min     — what the parameter equals at signal = 0
  - range_max     — what the parameter equals at signal = 1
  - invert        — swap range_min ↔ range_max
  - smooth_rise   — 0..1: how fast the route reacts when signal increases (1 = instant)
  - smooth_fall   — 0..1: how slowly it releases (low = long tail)

Result:
    raw       = audio[band] * gain
    gated     = max(0, raw - threshold) / (1 - threshold)         clamped to 0..1
    shaped    = gated^curve                                       (0..1)
    if invert: shaped = 1 - shaped
    smoothed  = previous + (shaped - previous) * (rise if shaped > prev else fall)
    effective = lerp(range_min, range_max, smoothed)
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass


# Audio bands (incl. derived "beat" onset spike) + tracking-derived signals
AUDIO_SOURCES = ("bass", "mid", "hi", "peak", "beat")
TRACKING_SOURCES = (
    "face_x", "face_y", "face_size", "face_count",
    "hand_left_x", "hand_left_y", "hand_left_open",
    "hand_right_x", "hand_right_y", "hand_right_open", "hand_count",
    "blob_x", "blob_y", "blob_size", "blob_count",
    "flow_mag", "pose_head_y", "pose_arms_up",
)
SOURCES = ("off",) + AUDIO_SOURCES + TRACKING_SOURCES


@dataclass
class ModRoute:
    target: str
    source: str = "off"          # "off" | one of SOURCES (except "off")
    gain: float = 1.0
    threshold: float = 0.05
    curve: float = 1.0
    range_min: float = 0.0
    range_max: float = 1.0
    invert: bool = False
    smooth_rise: float = 0.8
    smooth_fall: float = 0.15
    _smoothed: float = 0.0       # internal state


class ModulationEngine(threading.Thread):
    """Background tick that writes modulated effective values from audio bands."""

    def __init__(self, state, param_ranges: dict) -> None:
        super().__init__(name="ModulationEngine", daemon=True)
        self.state = state
        self.param_ranges = param_ranges   # {tag: (vmin, vmax)} — defines slider range
        self._tick_hz = 60.0

    def run(self) -> None:
        last = time.monotonic()
        while not self.state.stop_event.is_set():
            routes = self.state.snapshot_mod_routes()
            if routes:
                bands    = self.state.get_audio_bands()
                tracking = self.state.get_tracking()
                # merged source dict: audio bands + tracking values
                sources = dict(bands)
                sources.update(tracking)
                updates: dict[str, float] = {}
                for r in routes:
                    if r.source == "off" or r.source not in sources:
                        continue
                    raw = sources.get(r.source, 0.0) * max(0.0, r.gain)
                    thr = max(0.0, min(0.99, r.threshold))
                    gated = max(0.0, raw - thr) / max(1.0 - thr, 1e-3)
                    gated = max(0.0, min(1.0, gated))
                    curve = max(0.05, r.curve)
                    shaped = math.pow(gated, curve)
                    if r.invert:
                        shaped = 1.0 - shaped
                    # asymmetric smoothing
                    coef = r.smooth_rise if shaped > r._smoothed else r.smooth_fall
                    coef = max(0.001, min(1.0, coef))
                    r._smoothed += (shaped - r._smoothed) * coef
                    # map to parameter range
                    lo, hi = r.range_min, r.range_max
                    effective = lo + (hi - lo) * r._smoothed
                    # clamp to the param's slider range so we never exceed it
                    pmin, pmax = self.param_ranges.get(r.target, (lo, hi))
                    effective = max(pmin, min(pmax, effective))
                    updates[r.target] = effective

                if updates:
                    self.state.apply_modulated_params(updates)

            # honest 60Hz tick
            now = time.monotonic()
            sleep_for = (1.0 / self._tick_hz) - (now - last)
            last = now
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                time.sleep(0.001)
