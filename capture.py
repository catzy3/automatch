"""USB camera capture thread with backend fallback + optical flow.

Detection now runs in a separate DetectorThread (below) so heavy inference
(MediaPipe pose, hands, eye cascade, blob HSV scan) cannot stall the capture
loop. Capture pushes the most-recent frame into a single-slot slot for the
detector; older frames get dropped. The detector reads, processes, and writes
results back into SharedState.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

from detection import Detector
from state import SharedState


# Optical flow is computed on a small downsampled copy for speed.
# Output is normalized to UV space (so pixel_shift = flow * frame_size).
_FLOW_SIZE = (320, 180)


# Try in this order. ANY lets OpenCV pick whatever works.
_BACKENDS = [
    ("DSHOW", cv2.CAP_DSHOW),
    ("MSMF",  cv2.CAP_MSMF),
    ("ANY",   cv2.CAP_ANY),
]


def list_cameras(max_index: int = 5) -> List[Tuple[int, str]]:
    """Return [(index, friendly_name)] for cameras that opened successfully."""
    names: dict[int, str] = {}
    try:
        from pygrabber.dshow_graph import FilterGraph

        graph = FilterGraph()
        for i, n in enumerate(graph.get_input_devices()):
            names[i] = n
        print(f"[capture] pygrabber found: {list(names.items())}")
    except Exception as e:
        print(f"[capture] pygrabber unavailable ({e!s}); falling back to probing")

    found: List[Tuple[int, str]] = []
    for i in range(max_index):
        opened = False
        for backend_name, backend in _BACKENDS:
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                ok, _ = cap.read()
                cap.release()
                if ok:
                    found.append((i, names.get(i, f"Camera {i}")))
                    opened = True
                    print(f"[capture] index {i} OK via {backend_name}")
                    break
            else:
                cap.release()
        if not opened:
            print(f"[capture] index {i}: no working backend")
    if not found:
        print("[capture] WARNING: no cameras detected. "
              "Check Settings → Privacy → Camera (allow desktop apps), "
              "and make sure no other app is holding the device.")
    return found


def open_camera(index: int, width: int, height: int) -> cv2.VideoCapture | None:
    """Open camera with backend fallback. Returns None if nothing worked."""
    for backend_name, backend in _BACKENDS:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if ok and frame is not None:
                aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                print(f"[capture] opened device {index} via {backend_name} "
                      f"@ {aw}x{ah}")
                return cap
            cap.release()
        else:
            cap.release()
    print(f"[capture] failed to open device {index} on any backend")
    return None


def _make_flow_engine():
    """Build a DIS optical-flow engine (fast preset). None if unavailable."""
    try:
        engine = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        engine.setFinestScale(2)
        engine.setGradientDescentIterations(8)
        engine.setVariationalRefinementIterations(0)
        return engine
    except AttributeError:
        try:
            return cv2.optflow.createOptFlow_DIS(cv2.optflow.DISOPTICAL_FLOW_PRESET_FAST)
        except Exception:
            print("[capture] DIS optical flow unavailable — datamosh will fall back to freeze-only")
            return None


class CameraThread(threading.Thread):
    def __init__(self, state: SharedState) -> None:
        super().__init__(name="CaptureThread", daemon=True)
        self.state = state
        self._restart = threading.Event()

        self._flow_engine = _make_flow_engine()
        self._prev_gray = None  # downsampled grayscale for flow

        # Single-slot frame slot for the DetectorThread to read.
        # We don't queue — only the latest frame matters for tracking overlays.
        self._det_slot: Optional[np.ndarray] = None
        self._det_lock = threading.Lock()
        # Separate single-slot for DepthThread (it runs much less often).
        self._depth_slot: Optional[np.ndarray] = None
        self._depth_lock = threading.Lock()

    def request_restart(self) -> None:
        self._restart.set()
        # invalidate flow state on restart so we don't compute flow across a
        # discontinuity (resolution change, device change, etc.)
        self._prev_gray = None

    def pop_detection_frame(self) -> Optional[np.ndarray]:
        """Called by DetectorThread to fetch the latest frame for inference."""
        with self._det_lock:
            f = self._det_slot
            self._det_slot = None
            return f

    def pop_depth_frame(self) -> Optional[np.ndarray]:
        """Called by DepthThread for ONNX depth inference (lower-frequency)."""
        with self._depth_lock:
            f = self._depth_slot
            self._depth_slot = None
            return f

    def _compute_flow(self, frame_bgr: np.ndarray) -> None:
        if self._flow_engine is None:
            return
        small = cv2.resize(frame_bgr, _FLOW_SIZE, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            return
        try:
            flow = self._flow_engine.calc(self._prev_gray, gray, None)
        except cv2.error:
            self._prev_gray = gray
            return
        self._prev_gray = gray

        # Normalize: divide x by W, y by H, so 1.0 = full image dimension.
        w, h = _FLOW_SIZE
        flow = flow.astype(np.float32, copy=False)
        flow[..., 0] /= float(w)
        flow[..., 1] /= float(h)
        # Match GL orientation: render flips frame on Y, so flip flow rows + invert dy.
        flow = cv2.flip(flow, 0)
        flow[..., 1] = -flow[..., 1]
        # texture upload expects contiguous; also clip extreme values
        np.clip(flow, -0.25, 0.25, out=flow)
        self.state.set_flow(np.ascontiguousarray(flow))

    def run(self) -> None:
        cap: cv2.VideoCapture | None = None
        while not self.state.stop_event.is_set():
            params = self.state.snapshot_params()
            if not params.running:
                if cap is not None:
                    cap.release()
                    cap = None
                self._prev_gray = None
                time.sleep(0.05)
                continue

            if cap is None or self._restart.is_set():
                if cap is not None:
                    cap.release()
                self._restart.clear()
                cap = open_camera(params.device_index, params.width, params.height)
                if cap is None:
                    time.sleep(0.5)
                    continue

            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                cap = None
                self._prev_gray = None
                time.sleep(0.02)
                continue

            self.state.set_frame(frame)
            self._compute_flow(frame)

            # publish current frame for both background workers.
            # Single-slot — old frame is silently overwritten.
            with self._det_lock:
                self._det_slot = frame
            with self._depth_lock:
                self._depth_slot = frame

        if cap is not None:
            cap.release()


class DetectorThread(threading.Thread):
    """Run face / eye / pose / hands / blob inference asynchronously to the camera.

    Reads the latest frame from CameraThread via `pop_detection_frame()` and
    writes results into SharedState. Skips frames that are already stale so we
    never queue up backlog — if inference can't keep up, it just falls behind
    one frame, never multiple.
    """

    def __init__(self, state: SharedState, camera: CameraThread) -> None:
        super().__init__(name="DetectorThread", daemon=True)
        self.state = state
        self.camera = camera
        self._detector = Detector()
        self._last_sens: tuple | None = None

    def run(self) -> None:
        while not self.state.stop_event.is_set():
            params = self.state.snapshot_params()

            # apply sensitivity changes
            sens = (
                params.detect_min_confidence,
                params.detect_eye_sf,
                int(params.detect_eye_neighbors),
                bool(params.detect_face),
                bool(params.detect_eyes),
                bool(params.detect_pose),
                bool(params.detect_face_mesh),
                bool(params.detect_hands),
                bool(params.detect_blobs),
                round(params.blob_target_r, 3), round(params.blob_target_g, 3),
                round(params.blob_target_b, 3),
                round(params.blob_hue_tol, 1), round(params.blob_sat_min, 3),
                round(params.blob_val_min, 3),
                int(params.blob_area_min), int(params.blob_area_max),
                int(params.blob_max_count),
            )
            if sens != self._last_sens:
                self._detector.update_sensitivity(
                    min_confidence=sens[0], eye_sf=sens[1], eye_neighbors=sens[2],
                    enable_face=sens[3], enable_eyes=sens[4], enable_pose=sens[5],
                    enable_face_mesh=sens[6],
                    enable_hands=sens[7],
                    enable_blobs=sens[8],
                    blob_target_rgb=(sens[9], sens[10], sens[11]),
                    blob_hue_tol=sens[12], blob_sat_min=sens[13],
                    blob_val_min=sens[14], blob_area_min=sens[15],
                    blob_area_max=sens[16], blob_max_count=sens[17],
                )
                self._last_sens = sens

            frame = self.camera.pop_detection_frame()
            if frame is None or not self._detector.available:
                time.sleep(0.010)
                continue

            try:
                det_in = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
                det = self._detector.detect(det_in)
                det.width = frame.shape[1]
                det.height = frame.shape[0]
                self.state.set_detection(det)
                # derive tracking modulation values
                self._publish_tracking(det)
            except Exception as e:
                # detection error shouldn't kill the thread
                print(f"[detector] {e}")
                time.sleep(0.05)

    def _publish_tracking(self, det) -> None:
        """Compute 0..1 modulation values from detection + flow, write to state."""
        tr: dict[str, float] = {}

        # Face (first face, if any)
        if det.faces:
            f = det.faces[0]
            tr["face_x"]    = float(f.x + f.w * 0.5)         # 0..1 centroid
            tr["face_y"]    = float(f.y + f.h * 0.5)
            tr["face_size"] = float(min(1.0, max(f.w, f.h) * 2.0))
        else:
            tr["face_x"] = 0.5
            tr["face_y"] = 0.5
            tr["face_size"] = 0.0
        tr["face_count"] = min(1.0, len(det.faces) / 4.0)

        # Hands (left = first, right = second by x position)
        if det.hands:
            hands_sorted = sorted(det.hands, key=lambda h: float(h[0, 0]))
            for i, name in enumerate(("hand_left", "hand_right")):
                if i < len(hands_sorted):
                    h = hands_sorted[i]
                    # wrist = landmark 0
                    tr[f"{name}_x"] = float(h[0, 0])
                    tr[f"{name}_y"] = float(h[0, 1])
                    # openness: avg distance from fingertips (4,8,12,16,20) to wrist
                    tips = h[[4, 8, 12, 16, 20]]
                    wrist = h[0]
                    d = float(np.mean(np.linalg.norm(tips[:, :2] - wrist[:2], axis=1)))
                    tr[f"{name}_open"] = float(max(0.0, min(1.0, (d - 0.05) / 0.30)))
                else:
                    tr[f"{name}_x"] = 0.5
                    tr[f"{name}_y"] = 0.5
                    tr[f"{name}_open"] = 0.0
        else:
            for name in ("hand_left", "hand_right"):
                tr[f"{name}_x"] = 0.5
                tr[f"{name}_y"] = 0.5
                tr[f"{name}_open"] = 0.0
        tr["hand_count"] = min(1.0, len(det.hands) / 2.0)

        # Blobs (largest blob)
        if det.blobs:
            b = det.blobs[0]
            tr["blob_x"]    = float(b.cx)
            tr["blob_y"]    = float(b.cy)
            tr["blob_size"] = float(min(1.0, max(b.w, b.h) * 2.0))
        else:
            tr["blob_x"] = 0.5
            tr["blob_y"] = 0.5
            tr["blob_size"] = 0.0
        tr["blob_count"] = min(1.0, len(det.blobs) / 8.0)

        # Optical flow magnitude (mean over the latest flow frame)
        flow, _ = self.state.get_flow()
        if flow is not None:
            mag = float(np.mean(np.abs(flow))) * 30.0
            tr["flow_mag"] = max(0.0, min(1.0, mag))
        else:
            tr["flow_mag"] = 0.0

        # Pose head Y (lower number = head higher up)
        if det.pose is not None and det.pose.shape[0] > 0:
            tr["pose_head_y"] = float(1.0 - det.pose[0, 1])     # invert so high=up
            # arms up: are wrists (15, 16) higher than shoulders (11, 12)?
            try:
                lw = det.pose[15, 1]; ls = det.pose[11, 1]
                rw = det.pose[16, 1]; rs = det.pose[12, 1]
                up = 0.5 * (max(0.0, ls - lw) + max(0.0, rs - rw))
                tr["pose_arms_up"] = max(0.0, min(1.0, up * 2.0))
            except Exception:
                tr["pose_arms_up"] = 0.0
        else:
            tr["pose_head_y"] = 0.5
            tr["pose_arms_up"] = 0.0

        self.state.set_tracking(tr)
