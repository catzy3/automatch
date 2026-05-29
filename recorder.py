"""mp4 recorder: ffmpeg subprocess fed RGB frames from the render thread."""
from __future__ import annotations

import datetime as _dt
import queue
import shutil
import subprocess
import threading
from pathlib import Path


def _find_ffmpeg() -> str | None:
    # bundled via imageio-ffmpeg if present (cross-platform)
    try:
        from imageio_ffmpeg import get_ffmpeg_exe  # type: ignore
        return get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


class Recorder(threading.Thread):
    """Reads (w, h, rgb_bytes) tuples from a queue and writes an mp4.

    The render thread enqueues frames when state.recording is True.
    """

    def __init__(self, frame_queue: queue.Queue, out_dir: Path,
                 fps: int = 30) -> None:
        super().__init__(name="RecorderThread", daemon=True)
        self.frame_queue = frame_queue
        self.out_dir = out_dir
        self.fps = fps
        self.stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._size: tuple[int, int] | None = None
        self._path: Path | None = None
        self.last_path: Path | None = None

    def _start_ffmpeg(self, w: int, h: int) -> None:
        ffmpeg = _find_ffmpeg()
        if ffmpeg is None:
            print("[recorder] ffmpeg not found. Install imageio-ffmpeg "
                  "(`pip install imageio-ffmpeg`) or put ffmpeg in PATH.")
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = self.out_dir / f"analogvfx_{ts}.mp4"
        args = [
            ffmpeg, "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{w}x{h}",
            "-r", str(self.fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "veryfast",
            "-crf", "18",
            str(self._path),
        ]
        print(f"[recorder] starting → {self._path}")
        self._proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self._size = (w, h)

    def _stop_ffmpeg(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=10)
            except Exception as e:
                print(f"[recorder] error closing ffmpeg: {e}")
                try:
                    self._proc.kill()
                except Exception:
                    pass
            if self._path is not None:
                print(f"[recorder] saved {self._path}")
                self.last_path = self._path
        self._proc = None
        self._size = None
        self._path = None

    def stop_session(self) -> None:
        """Stop current recording session but keep thread alive."""
        self._stop_ffmpeg()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                item = self.frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                # sentinel: stop session
                self._stop_ffmpeg()
                continue
            w, h, rgb = item
            if self._proc is None or self._size != (w, h):
                # size changed mid-recording → close + reopen
                if self._proc is not None:
                    self._stop_ffmpeg()
                self._start_ffmpeg(w, h)
                if self._proc is None:
                    continue
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(rgb.tobytes())
            except BrokenPipeError:
                print("[recorder] ffmpeg pipe broken")
                self._stop_ffmpeg()
        self._stop_ffmpeg()
