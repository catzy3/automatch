"""AnalogVFX — live analog distortions + audio-reactive + Spout + mp4."""
from __future__ import annotations

import queue
import sys
from pathlib import Path

from audio import AudioThread
from capture import CameraThread, DetectorThread
from depth import DepthThread
from gui import EFFECT_PARAMS, GLOBAL_PARAMS, build_gui
from modulation import ModulationEngine
from recorder import Recorder
from renderer import RenderThread
from state import EFFECT_KINDS, SharedState


REC_DIR = Path(__file__).parent / "recordings"
LAST_SESSION = Path(__file__).parent / "last_session.json"


def _build_param_ranges() -> dict[str, tuple]:
    """Flatten the GUI's per-effect parameter ranges into one lookup dict.
    Color groups (tag is a 3-tuple of R/G/B tags) expand into three entries.
    Text-input entries (vmin/vmax both None) are skipped — they aren't numeric.
    Also includes virtual 'effect_mix_{kind}' targets used to modulate the
    dry/wet slider of each effect."""
    out: dict[str, tuple] = {}
    for _, tag, vmin, vmax in GLOBAL_PARAMS:
        out[tag] = (float(vmin), float(vmax))
    for group in EFFECT_PARAMS.values():
        for _, tag, vmin, vmax in group:
            if isinstance(tag, tuple):
                # color group → each channel is 0..1
                for t in tag:
                    out[t] = (float(vmin), float(vmax))
            elif vmin is None or vmax is None:
                # text input — not a numeric param
                continue
            else:
                out[tag] = (float(vmin), float(vmax))
    for kind in EFFECT_KINDS:
        out[f"effect_mix_{kind}"] = (0.0, 1.0)
    return out


def main() -> int:
    state = SharedState()

    # auto-restore last session if present
    if LAST_SESSION.exists():
        try:
            before = {e.kind for e in state.params.effects}
            state.load_preset(LAST_SESSION)
            after = {e.kind for e in state.params.effects}
            new_kinds = after - before
            print(f"[main] restored last session from {LAST_SESSION}")
            if new_kinds:
                print(f"[main] merged new effects (disabled): {sorted(new_kinds)}")
        except Exception as e:
            print(f"[main] failed to restore last session: {e}")

    frame_q: queue.Queue = queue.Queue(maxsize=8)
    recorder = Recorder(frame_q, REC_DIR, fps=30)
    capture = CameraThread(state)
    detector = DetectorThread(state, capture)
    depth = DepthThread(state, capture)
    renderer = RenderThread(state, recorder_queue=frame_q)
    audio = AudioThread(state)
    mod_engine = ModulationEngine(state, _build_param_ranges())

    recorder.start()
    capture.start()
    detector.start()
    depth.start()
    renderer.start()
    audio.start()
    mod_engine.start()

    if not renderer.wait_gl_ready(timeout=10.0):
        print("renderer failed to initialize OpenGL — exiting")
        state.stop_event.set()
        return 1

    def on_record_toggle(active: bool) -> None:
        if not active:
            try:
                frame_q.put_nowait(None)
            except queue.Full:
                pass

    try:
        build_gui(state, capture, on_record_toggle=on_record_toggle,
                  audio_thread=audio, param_ranges=_build_param_ranges())
    finally:
        # always autosave current state, regardless of how we got here
        try:
            state.save_preset(LAST_SESSION)
            print(f"[main] last session saved → {LAST_SESSION}")
        except Exception as e:
            print(f"[main] autosave failed: {e}")

        state.stop_event.set()
        try:
            frame_q.put_nowait(None)
        except queue.Full:
            pass
        recorder.stop_event.set()
        capture.join(timeout=2.0)
        detector.join(timeout=2.0)
        depth.join(timeout=3.0)
        renderer.join(timeout=5.0)
        recorder.join(timeout=10.0)
        audio.join(timeout=2.0)
        mod_engine.join(timeout=2.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
