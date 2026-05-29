"""Audio capture + spectral analysis.

Source can be:
  - the system default input (usually a mic), or
  - any input device by index, or
  - the system OUTPUT loopback (WASAPI on Windows) — captures what your
    computer is *playing* (browser audio, Spotify, games, etc.) without any
    third-party cable software.

Writes 4 normalized bands into SharedState.audio_bands:

    "bass"  — 20..250 Hz
    "mid"   — 250 Hz .. 2 kHz
    "hi"    — 2 kHz .. 8 kHz
    "peak"  — instantaneous peak amplitude (full band)

Each band has attack/release smoothing.

If sounddevice / PortAudio is missing, the thread quietly stays in "no audio"
state and bands report 0.0.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import List, Optional, Tuple

import numpy as np

try:
    import sounddevice as sd  # type: ignore
    HAS_SD = True
except Exception as e:
    HAS_SD = False
    _IMPORT_ERR = str(e)


SAMPLE_RATE = 44100
BLOCK_SIZE  = 1024
WINDOW_SIZE = 2048

BAND_EDGES = {
    "bass": (20.0,   250.0),
    "mid":  (250.0,  2000.0),
    "hi":   (2000.0, 8000.0),
}


# -------------- device enumeration --------------

def list_devices() -> List[Tuple[int, str, str]]:
    """Return [(index, friendly_name, kind)] where kind ∈ {"input", "output"}.

    Outputs are included so they can be used as WASAPI loopback sources to
    capture system audio. A single physical device that has both input and
    output channels (USB headset, etc.) will appear twice — once per role.

    We prefer the WASAPI hostapi entries when available because that's the only
    backend that supports loopback on Windows; if multiple hostapis expose the
    same name, the WASAPI one is listed first.
    """
    if not HAS_SD:
        return []
    try:
        devs = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as e:
        print(f"[audio] query_devices failed: {e}")
        return []

    def hostapi_name(idx: int) -> str:
        try:
            return hostapis[idx]["name"]
        except Exception:
            return ""

    inputs: list[tuple[int, str, str]] = []
    outputs: list[tuple[int, str, str]] = []
    for i, d in enumerate(devs):
        name = d.get("name", f"dev {i}")
        hapi = hostapi_name(d.get("hostapi", -1))
        label = f"{name}  · {hapi}" if hapi else name
        if d.get("max_input_channels", 0) > 0:
            inputs.append((i, label, "input"))
        if d.get("max_output_channels", 0) > 0:
            outputs.append((i, label, "output"))

    # Sort outputs so WASAPI ones come first (they're the loopback-capable ones).
    outputs.sort(key=lambda t: (0 if "WASAPI" in t[1] else 1, t[0]))
    return inputs + outputs


def find_default_loopback() -> Optional[int]:
    """Find the system default OUTPUT device index — usable as WASAPI loopback."""
    if not HAS_SD:
        return None
    try:
        idx = sd.default.device[1]  # default output
        if idx is None or idx < 0:
            return None
        return int(idx)
    except Exception:
        return None


# -------------- capture thread --------------

class AudioThread(threading.Thread):
    def __init__(self, state) -> None:
        super().__init__(name="AudioThread", daemon=True)
        self.state = state
        self._buf = deque(maxlen=WINDOW_SIZE)
        self._stream: Optional[object] = None
        self._smoothed = {"bass": 0.0, "mid": 0.0, "hi": 0.0, "peak": 0.0, "beat": 0.0}
        self._enabled = True
        # bass-energy history for onset (beat) detection
        self._bass_hist = deque(maxlen=48)   # ~1 sec of bass values at 50 Hz
        self._beat_state = 0.0
        self._beat_cooldown = 0
        # current configuration (set via reconfigure)
        self._device_index: Optional[int] = None     # None = system default
        self._loopback = False
        # request flag — main loop reopens the stream when it changes
        self._request_reopen = threading.Event()
        self._cfg_lock = threading.Lock()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)

    def reconfigure(self, *, device_index: Optional[int], loopback: bool) -> None:
        with self._cfg_lock:
            self._device_index = device_index
            self._loopback = bool(loopback)
        self._request_reopen.set()

    # ----- core -----

    def _audio_cb(self, indata, frames, time_info, status):
        if indata.shape[1] > 1:
            mono = indata.mean(axis=1)
        else:
            mono = indata[:, 0]
        self._buf.extend(mono.tolist())

    def _analyze(self) -> dict:
        n = len(self._buf)
        if n < 512:
            return {"bass": 0.0, "mid": 0.0, "hi": 0.0, "peak": 0.0}
        x = np.fromiter(self._buf, dtype=np.float32, count=n)
        win = np.hanning(n).astype(np.float32)
        xw = x * win
        spec = np.abs(np.fft.rfft(xw))
        freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)
        spec /= (win.sum() + 1e-6)

        out = {}
        for name, (lo, hi) in BAND_EDGES.items():
            mask = (freqs >= lo) & (freqs < hi)
            if not mask.any():
                out[name] = 0.0
                continue
            band_energy = float(spec[mask].mean())
            out[name] = float(np.tanh(band_energy * 40.0))
        out["peak"] = float(np.tanh(np.abs(x).max() * 5.0))

        # ---- beat / onset detection from bass energy ----
        # When the current bass exceeds the running mean by > k×std, we count it
        # as an onset. The beat output is a fast spike that decays.
        self._bass_hist.append(out["bass"])
        if len(self._bass_hist) >= 16:
            arr = np.fromiter(self._bass_hist, dtype=np.float32,
                              count=len(self._bass_hist))
            mean = arr.mean()
            std  = arr.std() + 1e-3
            score = (out["bass"] - mean) / std
            # detect onset
            if score > 1.8 and self._beat_cooldown <= 0:
                self._beat_state = 1.0
                self._beat_cooldown = 6      # ~120 ms refractory
        if self._beat_cooldown > 0:
            self._beat_cooldown -= 1
        out["beat"] = self._beat_state
        # exponential decay for beat
        self._beat_state *= 0.75
        return out

    def _smooth(self, raw: dict) -> dict:
        attack  = {"bass": 0.7, "mid": 0.7, "hi": 0.8, "peak": 0.9, "beat": 1.0}
        release = {"bass": 0.06,"mid": 0.10,"hi": 0.15,"peak": 0.20, "beat": 0.30}
        out = {}
        for k, v in raw.items():
            prev = self._smoothed.get(k, 0.0)
            if v > prev:
                prev = prev + (v - prev) * attack[k]
            else:
                prev = prev + (v - prev) * release[k]
            self._smoothed[k] = prev
            out[k] = prev
        return out

    # ----- stream lifecycle -----

    def _open_stream(self) -> Optional[object]:
        with self._cfg_lock:
            device_index = self._device_index
            loopback = self._loopback
        try:
            extra = None
            if loopback:
                # WASAPI loopback — capture from an OUTPUT device
                try:
                    extra = sd.WasapiSettings(loopback=True)  # type: ignore
                except Exception as e:
                    print(f"[audio] WASAPI loopback not available ({e}); "
                          "falling back to regular input.")
                    extra = None
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                channels=1 if not loopback else 2,
                dtype="float32",
                device=device_index,
                callback=self._audio_cb,
                extra_settings=extra,
            )
            stream.start()
            # describe what we actually opened
            try:
                info = sd.query_devices(device_index) if device_index is not None else sd.query_devices(sd.default.device[0])
                tag = info["name"]
            except Exception:
                tag = f"device={device_index}"
            kind = "OUTPUT loopback" if loopback else "INPUT"
            print(f"[audio] capture started @ {SAMPLE_RATE} Hz from {kind}: {tag}")
            return stream
        except Exception as e:
            print(f"[audio] failed to open input stream ({e}). "
                  f"device_index={device_index}, loopback={loopback}")
            return None

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._buf.clear()

    # ----- run loop -----

    def run(self) -> None:
        if not HAS_SD:
            print(f"[audio] sounddevice unavailable ({_IMPORT_ERR}); "
                  "audio reactivity disabled. `pip install sounddevice` to enable.")
            return

        self._stream = self._open_stream()

        try:
            while not self.state.stop_event.is_set():
                if self._request_reopen.is_set():
                    self._request_reopen.clear()
                    self._close_stream()
                    self._stream = self._open_stream()

                if self._enabled:
                    raw = self._analyze()
                    smoothed = self._smooth(raw)
                else:
                    smoothed = self._smooth({"bass": 0.0, "mid": 0.0, "hi": 0.0,
                                             "peak": 0.0, "beat": 0.0})
                self.state.set_audio_bands(smoothed)
                time.sleep(0.020)
        finally:
            self._close_stream()
