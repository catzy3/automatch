"""Depth estimation thread.

Tries to load Depth-Anything-v2-small (~50 MB ONNX) from a few mirrors.
If that fails, falls back to a *fake* depth map derived from face detection:
pixels inside a face bbox count as foreground (depth=0), everything else as
background (depth=1) with a soft falloff. Not pretty, but enough to drive a
depth_mask without any neural model.

Output: `state.depth_map` — float32 ndarray (H, W), values 0..1, where 0 is
NEAR and 1 is FAR.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


_MODEL_DIR = Path(__file__).parent / "models"
_MODEL_NAME = "depth_anything_v2_small.onnx"

# A few mirrors to try, in priority order.
_MODEL_URLS = [
    "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx",
    "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_fp16.onnx",
]


def _download(url: str, dst: Path) -> bool:
    try:
        print(f"[depth] downloading from {url}")
        opener = urllib.request.build_opener()
        opener.addheaders = [("User-Agent", "AnalogVFX/1.0")]
        urllib.request.install_opener(opener)
        urllib.request.urlretrieve(url, dst)
        size = dst.stat().st_size
        if size < 100_000:
            print(f"[depth]   suspicious file size {size} B — discarding")
            dst.unlink(missing_ok=True)
            return False
        print(f"[depth]   saved → {dst} ({size/1_000_000:.1f} MB)")
        return True
    except Exception as e:
        print(f"[depth]   failed: {e}")
        return False


def _load_model() -> Optional[object]:
    """Returns either an onnxruntime InferenceSession or a cv2.dnn Net,
    or None if nothing loaded. The DepthThread auto-detects which it got
    by `hasattr(net, "run")` (onnxruntime) vs cv2.dnn API."""
    _MODEL_DIR.mkdir(exist_ok=True)
    path = _MODEL_DIR / _MODEL_NAME
    if not path.exists():
        for url in _MODEL_URLS:
            if _download(url, path):
                break
    if not path.exists():
        return None

    # 1) Prefer onnxruntime — handles dynamic shapes (which Depth-Anything-v2 uses).
    try:
        import onnxruntime as ort  # type: ignore
        providers = ["CPUExecutionProvider"]
        sess = ort.InferenceSession(str(path), providers=providers)
        print(f"[depth] onnxruntime session opened, providers={providers}")
        return sess
    except ImportError:
        print("[depth] onnxruntime not installed; trying cv2.dnn fallback")
    except Exception as e:
        print(f"[depth] onnxruntime load failed ({e}); trying cv2.dnn")

    # 2) Fallback: cv2.dnn (only works for static-shape ONNX)
    try:
        net = cv2.dnn.readNetFromONNX(str(path))
        print("[depth] cv2.dnn depth model loaded")
        return net
    except Exception as e:
        print(f"[depth] cv2.dnn failed: {e}")
        print("[depth] tip: `pip install onnxruntime` to enable depth inference")
        return None


class DepthThread(threading.Thread):
    INPUT_SIZE = 256        # MiDaS-class models like 256 or 384
    UPDATE_HZ  = 6.0        # depth at ~6 fps is enough for a slow mask

    def __init__(self, state, camera) -> None:
        super().__init__(name="DepthThread", daemon=True)
        self.state = state
        self.camera = camera
        self._net = None
        self._fallback_active = False
        self._load_done = threading.Event()

    def _lazy_load_if_needed(self) -> None:
        if self._load_done.is_set():
            return
        params = self.state.snapshot_params()
        if not getattr(params, "depth_enabled", False):
            return
        self._net = _load_model()
        self._fallback_active = self._net is None
        if self._fallback_active:
            print("[depth] no model — using face-bbox fallback")
        self._load_done.set()

    # -------- real depth --------

    def _infer(self, frame_bgr: np.ndarray) -> np.ndarray:
        N = self.INPUT_SIZE
        small = cv2.resize(frame_bgr, (N, N), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std  = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        rgb = (rgb - mean) / std
        blob = rgb.transpose(2, 0, 1)[None].astype(np.float32)   # (1, 3, N, N)

        # dispatch on backend type
        if hasattr(self._net, "run"):
            # onnxruntime InferenceSession
            input_name = self._net.get_inputs()[0].name
            outputs = self._net.run(None, {input_name: blob})
            out = outputs[0]
        else:
            # cv2.dnn Net
            self._net.setInput(blob)
            out = self._net.forward()
        out = np.squeeze(out)
        # normalize 0..1
        mn, mx = float(out.min()), float(out.max())
        if mx - mn < 1e-6:
            return np.zeros_like(out)
        depth = (out - mn) / (mx - mn)
        # Depth-Anything outputs *near=high*, but we want NEAR=0, FAR=1.
        depth = 1.0 - depth
        # upscale to frame size
        H, W = frame_bgr.shape[:2]
        depth_full = cv2.resize(depth, (W, H), interpolation=cv2.INTER_LINEAR)
        return depth_full.astype(np.float32)

    # -------- fake depth from face boxes --------

    def _fake_from_faces(self, frame_bgr: np.ndarray) -> np.ndarray:
        H, W = frame_bgr.shape[:2]
        depth = np.ones((H, W), dtype=np.float32)   # all FAR by default
        det, _ = self.state.get_detection()
        if det is None or not det.faces:
            return depth
        for face in det.faces:
            cx = (face.x + face.w * 0.5) * W
            cy = (face.y + face.h * 0.5) * H
            radius = max(face.w * W, face.h * H) * 0.9
            ys, xs = np.ogrid[:H, :W]
            dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(radius, 1.0)
            mask = np.clip(dist, 0.0, 1.0).astype(np.float32)
            # mask: 0 at face centre → 1 at radius
            depth = np.minimum(depth, mask)
        return depth

    # -------- main loop --------

    def run(self) -> None:
        interval = 1.0 / max(self.UPDATE_HZ, 0.5)
        last_run = 0.0
        while not self.state.stop_event.is_set():
            params = self.state.snapshot_params()
            if not getattr(params, "depth_enabled", False):
                time.sleep(0.15)
                continue

            self._lazy_load_if_needed()
            now = time.monotonic()
            if now - last_run < interval:
                time.sleep(0.02)
                continue
            last_run = now

            frame = self.camera.pop_depth_frame()
            if frame is None:
                time.sleep(0.05)
                continue

            try:
                if self._net is not None:
                    depth = self._infer(frame)
                else:
                    depth = self._fake_from_faces(frame)
                self.state.set_depth_map(depth)
            except Exception as e:
                print(f"[depth] inference error: {e}")
                time.sleep(0.1)
