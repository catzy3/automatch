"""Face / eye / emotion / pose detector.

Backends, in priority order:
  - MediaPipe (face + pose + face mesh), if importable
  - cv2.FaceDetectorYN (always works in modern OpenCV) for face boxes

Eye detection: cv2 haar cascade (`haarcascade_eye.xml`) run inside each face
bounding box. Returns (x, y, w, h) per eye in normalized frame coordinates.

Emotion detection (optional): FER+ ONNX model. Downloaded to models/ on demand.
Outputs one of 8 labels per face: neutral, happiness, surprise, sadness, anger,
disgust, fear, contempt.

Sensitivity is exposed via update_sensitivity() so the GUI can tune confidence
and the per-frame detection rate without recreating the detector.
"""
from __future__ import annotations

import dataclasses
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np


_MODEL_DIR = Path(__file__).parent / "models"


# --------------------------------------------------- result dataclasses ----

@dataclass
class FaceBox:
    x: float           # 0..1 normalized
    y: float
    w: float
    h: float
    keypoints: List[Tuple[float, float]] = field(default_factory=list)
    eyes: List[Tuple[float, float, float, float]] = field(default_factory=list)
    pupils: List[Tuple[float, float]] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class Blob:
    x: float           # 0..1 normalized bbox x
    y: float
    w: float
    h: float
    cx: float          # 0..1 centroid
    cy: float
    area_px: int       # area in source pixels


@dataclass
class DetectionResult:
    faces: List[FaceBox] = field(default_factory=list)
    pose: Optional[np.ndarray] = None
    face_mesh: Optional[np.ndarray] = None
    hands: List[np.ndarray] = field(default_factory=list)
    blobs: List[Blob] = field(default_factory=list)
    width: int = 0
    height: int = 0


# --------------------------------------------------- loaders ----

def _load_eye_cascade():
    try:
        path = cv2.data.haarcascades + "haarcascade_eye.xml"
        cas = cv2.CascadeClassifier(path)
        if cas.empty():
            return None
        return cas
    except Exception as e:
        print(f"[detection] eye cascade load failed: {e}")
        return None


def _load_yunet():
    _MODEL_DIR.mkdir(exist_ok=True)
    model_path = _MODEL_DIR / "face_detection_yunet_2023mar.onnx"
    if not model_path.exists():
        url = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
               "face_detection_yunet/face_detection_yunet_2023mar.onnx")
        print(f"[detection] downloading YuNet → {model_path}")
        try:
            urllib.request.urlretrieve(url, model_path)
        except Exception as e:
            print(f"[detection] YuNet download failed: {e}")
            return None
    return cv2.FaceDetectorYN.create(str(model_path), "", (320, 320),
                                     0.6, 0.3, 5000)


# --------------------------------------------------- Detector ----

class Detector:
    """Thread-safe detector with sensitivity controls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mediapipe_loaded = False
        self.available = False

        self._mp_face = None
        self._mp_pose = None
        self._mp_mesh = None
        self._mp_hands = None
        self._yunet = None
        self._eye_cas = None

        # configurable
        self._min_confidence = 0.6
        self._eye_sf = 1.1          # scaleFactor
        self._eye_neighbors = 5
        self._enable_face = True
        self._enable_eyes = True
        self._enable_pose = True
        self._enable_face_mesh = False
        self._enable_hands = False

        # Blob detection config (HSV target + thresholds)
        self._blob_enable = False
        self._blob_target_h = 0          # 0..180 hue
        self._blob_hue_tol  = 12         # 0..90
        self._blob_sat_min  = 76         # 0..255
        self._blob_val_min  = 76         # 0..255
        self._blob_area_min = 200
        self._blob_area_max = 80000
        self._blob_max_count = 8

        # try mediapipe
        try:
            import mediapipe as mp  # type: ignore
            self._mp = mp
            self._mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=self._min_confidence)
            self._mp_pose = mp.solutions.pose.Pose(
                model_complexity=1, enable_segmentation=False,
                min_detection_confidence=self._min_confidence,
                min_tracking_confidence=0.5)
            self._mp_mesh = mp.solutions.face_mesh.FaceMesh(
                max_num_faces=3, refine_landmarks=False,
                min_detection_confidence=self._min_confidence,
                min_tracking_confidence=0.5)
            try:
                self._mp_hands = mp.solutions.hands.Hands(
                    max_num_hands=2, model_complexity=0,
                    min_detection_confidence=self._min_confidence,
                    min_tracking_confidence=0.5)
                print("[detection] MediaPipe Hands ready")
            except Exception as e:
                self._mp_hands = None
                print(f"[detection] MediaPipe Hands unavailable: {e}")
            self.mediapipe_loaded = True
            self.available = True
            print("[detection] using MediaPipe")
        except Exception as e:
            print(f"[detection] MediaPipe unavailable ({e!s}); using cv2 YuNet")

        # YuNet is always loaded as a fallback / for face boxes used by emotion crop
        if not self.mediapipe_loaded:
            self._yunet = _load_yunet()
            if self._yunet is not None:
                self.available = True

        # eye cascade
        self._eye_cas = _load_eye_cascade()
        if self._eye_cas is not None:
            print("[detection] eye cascade ready")

    # ---- configuration ----

    def update_sensitivity(self, *, min_confidence=None, eye_sf=None,
                           eye_neighbors=None, enable_face=None,
                           enable_eyes=None, enable_pose=None,
                           enable_face_mesh=None,
                           enable_hands=None,
                           enable_blobs=None, blob_target_rgb=None,
                           blob_hue_tol=None, blob_sat_min=None,
                           blob_val_min=None, blob_area_min=None,
                           blob_area_max=None, blob_max_count=None):
        with self._lock:
            if min_confidence is not None:
                self._min_confidence = float(min_confidence)
                # MediaPipe detectors store this internally; rebuild them
                if self.mediapipe_loaded:
                    try:
                        self._mp_face = self._mp.solutions.face_detection.FaceDetection(
                            model_selection=1, min_detection_confidence=self._min_confidence)
                    except Exception:
                        pass
            if eye_sf is not None:        self._eye_sf = max(1.01, float(eye_sf))
            if eye_neighbors is not None: self._eye_neighbors = max(1, int(eye_neighbors))
            if enable_face is not None:        self._enable_face = bool(enable_face)
            if enable_eyes is not None:        self._enable_eyes = bool(enable_eyes)
            if enable_pose is not None:        self._enable_pose = bool(enable_pose)
            if enable_face_mesh is not None:   self._enable_face_mesh = bool(enable_face_mesh)
            if enable_hands is not None:       self._enable_hands = bool(enable_hands) and self._mp_hands is not None
            if enable_blobs is not None:       self._blob_enable = bool(enable_blobs)
            if blob_target_rgb is not None:
                r, g, b = blob_target_rgb
                target_bgr = np.uint8([[[int(b*255), int(g*255), int(r*255)]]])
                hsv = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2HSV)
                self._blob_target_h = int(hsv[0, 0, 0])
            if blob_hue_tol is not None:       self._blob_hue_tol = int(blob_hue_tol)
            if blob_sat_min is not None:       self._blob_sat_min = int(blob_sat_min * 255)
            if blob_val_min is not None:       self._blob_val_min = int(blob_val_min * 255)
            if blob_area_min is not None:      self._blob_area_min = int(blob_area_min)
            if blob_area_max is not None:      self._blob_area_max = int(blob_area_max)
            if blob_max_count is not None:     self._blob_max_count = int(blob_max_count)

    # ---- main entry ----

    def detect(self, frame_bgr: np.ndarray) -> DetectionResult:
        with self._lock:
            h, w = frame_bgr.shape[:2]
            result = DetectionResult(width=w, height=h)
            if not self.available:
                return result

            gray_full = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

            # ---- face detection ----
            if self._enable_face:
                if self.mediapipe_loaded:
                    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    try:
                        fd = self._mp_face.process(rgb)
                        if fd and fd.detections:
                            for d in fd.detections:
                                box = d.location_data.relative_bounding_box
                                kps = [(kp.x, kp.y) for kp in d.location_data.relative_keypoints]
                                result.faces.append(FaceBox(
                                    x=box.xmin, y=box.ymin,
                                    w=box.width, h=box.height,
                                    keypoints=kps,
                                    confidence=d.score[0] if d.score else 1.0,
                                ))
                    except Exception:
                        pass
                elif self._yunet is not None:
                    try:
                        self._yunet.setScoreThreshold(self._min_confidence)
                        self._yunet.setInputSize((w, h))
                        _, faces = self._yunet.detect(frame_bgr)
                        if faces is not None:
                            for f in faces:
                                fx, fy, fw, fh = f[:4]
                                kps = [(f[4 + i*2] / w, f[4 + i*2 + 1] / h) for i in range(5)]
                                result.faces.append(FaceBox(
                                    x=fx / w, y=fy / h,
                                    w=fw / w, h=fh / h,
                                    keypoints=kps,
                                    confidence=float(f[14]) if len(f) > 14 else 1.0,
                                ))
                    except Exception:
                        pass

            # ---- eye detection per face (haar cascade) ----
            if self._enable_eyes and self._eye_cas is not None:
                for face in result.faces:
                    fx = int(face.x * w); fy = int(face.y * h)
                    fw = int(face.w * w); fh = int(face.h * h)
                    if fw < 30 or fh < 30:
                        continue
                    # focus on the upper 60% of the face
                    roi_y2 = fy + int(fh * 0.6)
                    roi_y1 = max(0, fy)
                    roi_x1 = max(0, fx)
                    roi_x2 = min(w, fx + fw)
                    roi = gray_full[roi_y1:roi_y2, roi_x1:roi_x2]
                    if roi.size == 0:
                        continue
                    try:
                        eyes = self._eye_cas.detectMultiScale(
                            roi, scaleFactor=self._eye_sf,
                            minNeighbors=self._eye_neighbors,
                            minSize=(max(8, fw // 14), max(8, fh // 14)),
                        )
                    except cv2.error:
                        eyes = []
                    for (ex, ey, ew, eh) in eyes:
                        gx = (roi_x1 + ex) / w
                        gy = (roi_y1 + ey) / h
                        gw = ew / w
                        gh = eh / h
                        face.eyes.append((gx, gy, gw, gh))
                        face.pupils.append((gx + gw * 0.5, gy + gh * 0.5))
                    # always keep at most 2 eyes, picking the largest by area
                    if len(face.eyes) > 2:
                        order = sorted(range(len(face.eyes)),
                                       key=lambda i: -(face.eyes[i][2] * face.eyes[i][3]))
                        face.eyes = [face.eyes[i] for i in order[:2]]
                        face.pupils = [face.pupils[i] for i in order[:2]]

            # ---- pose / face mesh (mediapipe only) ----
            if self.mediapipe_loaded:
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                if self._enable_pose and self._mp_pose is not None:
                    try:
                        pr = self._mp_pose.process(rgb)
                        if pr and pr.pose_landmarks:
                            lm = pr.pose_landmarks.landmark
                            result.pose = np.array(
                                [(p.x, p.y, p.visibility) for p in lm],
                                dtype=np.float32)
                    except Exception:
                        pass
                if self._enable_face_mesh and self._mp_mesh is not None:
                    try:
                        fm = self._mp_mesh.process(rgb)
                        if fm and fm.multi_face_landmarks:
                            lm = fm.multi_face_landmarks[0].landmark
                            result.face_mesh = np.array(
                                [(p.x, p.y) for p in lm], dtype=np.float32)
                    except Exception:
                        pass
                if self._enable_hands and self._mp_hands is not None:
                    try:
                        hr = self._mp_hands.process(rgb)
                        if hr and hr.multi_hand_landmarks:
                            for h in hr.multi_hand_landmarks:
                                lm = h.landmark
                                arr = np.array(
                                    [(p.x, p.y, getattr(p, "z", 0.0)) for p in lm],
                                    dtype=np.float32)
                                result.hands.append(arr)
                    except Exception:
                        pass

            # ---- blob detection (HSV connected components) ----
            if self._blob_enable:
                try:
                    self._detect_blobs(frame_bgr, result)
                except Exception as e:
                    print(f"[detection] blob detection error: {e}")

            return result

    def _detect_blobs(self, frame_bgr, result: "DetectionResult") -> None:
        H, W = frame_bgr.shape[:2]
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        target = int(self._blob_target_h)
        tol = int(self._blob_hue_tol)
        sat_min = self._blob_sat_min
        val_min = self._blob_val_min

        # Hue wraps around at 180; build the mask carefully
        lo_h = target - tol
        hi_h = target + tol
        if lo_h < 0 or hi_h > 180:
            lo1 = (lo_h % 180); hi1 = 180
            lo2 = 0;            hi2 = (hi_h % 180)
            mask1 = cv2.inRange(hsv, (lo1, sat_min, val_min), (hi1, 255, 255))
            mask2 = cv2.inRange(hsv, (lo2, sat_min, val_min), (hi2, 255, 255))
            mask = cv2.bitwise_or(mask1, mask2)
        else:
            mask = cv2.inRange(hsv, (lo_h, sat_min, val_min),
                                    (hi_h, 255, 255))

        # morphology to clean small noise + fill holes
        k = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

        n, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        # stats[i] = [x, y, w, h, area]; index 0 is background
        cand = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < self._blob_area_min or area > self._blob_area_max:
                continue
            cx, cy = centroids[i]
            cand.append((area, x, y, w, h, cx, cy))
        cand.sort(reverse=True)   # biggest first
        cand = cand[:max(1, self._blob_max_count)]
        for area, x, y, w, h, cx, cy in cand:
            result.blobs.append(Blob(
                x=x/W, y=y/H, w=w/W, h=h/H,
                cx=cx/W, cy=cy/H, area_px=int(area),
            ))

    def close(self) -> None:
        with self._lock:
            for obj in (self._mp_face, self._mp_pose, self._mp_mesh):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass


# Hand skeleton (MediaPipe 21-point per hand)
HAND_CONNECTIONS = [
    # thumb
    (0, 1), (1, 2), (2, 3), (3, 4),
    # index
    (0, 5), (5, 6), (6, 7), (7, 8),
    # middle
    (0, 9), (9, 10), (10, 11), (11, 12),
    # ring
    (0, 13), (13, 14), (14, 15), (15, 16),
    # pinky
    (0, 17), (17, 18), (18, 19), (19, 20),
    # palm
    (5, 9), (9, 13), (13, 17),
]


# Pose skeleton (MediaPipe 33-point)
POSE_CONNECTIONS = [
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),
    (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),
    (0, 1), (1, 2), (2, 3), (3, 7),
    (0, 4), (4, 5), (5, 6), (6, 8),
    (9, 10),
]
