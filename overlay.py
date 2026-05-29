"""CPU-side HUD overlay renderer.

Given DetectionResult + accent color + flags, draws boxes, dots, lines, text
into an RGBA numpy buffer. The renderer thread uploads this buffer as a
texture and the `hud_overlay` shader composits it over the processed frame.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from detection import DetectionResult, HAND_CONNECTIONS, POSE_CONNECTIONS


@dataclass
class OverlayConfig:
    show_face_box: bool = True
    show_face_keypoints: bool = True
    show_face_mesh: bool = False
    show_pose: bool = True
    show_vectors: bool = True
    show_text: bool = True
    show_crosshairs: bool = False
    show_eyes: bool = True
    show_pupils: bool = True
    show_hands: bool = True
    show_blobs: bool = True
    accent_bgr: tuple = (26, 107, 255)
    line_thickness: int = 2
    dot_radius: int = 3
    font_scale: float = 0.45
    glow: bool = True
    # user-editable HUD text templates
    text_top_left: str     = "%TC%   FACES %FACES%"
    text_top_right: str    = "TRACKING"
    text_bottom_left: str  = "FRM %FRAME%"
    text_bottom_right: str = "RES %WIDTH%x%HEIGHT%"
    text_face_label: str   = "FACE %INDEX%"
    text_blob_label: str   = "BLOB %INDEX% A%AREA%"


def _fmt(template: str, ctx: dict) -> str:
    """Expand %KEY% placeholders. Missing keys are left untouched."""
    out = template
    for k, v in ctx.items():
        out = out.replace(f"%{k}%", str(v))
    return out


# Crosshair corners (used when show_crosshairs=True)
def _draw_crosshair(canvas, cx, cy, size, color, thickness):
    cv2.line(canvas, (cx - size, cy), (cx + size, cy), color, thickness, cv2.LINE_AA)
    cv2.line(canvas, (cx, cy - size), (cx, cy + size), color, thickness, cv2.LINE_AA)


_RUN_START = time.monotonic()


def render_overlay(canvas: np.ndarray, det: DetectionResult,
                   cfg: OverlayConfig, frame_idx: int) -> None:
    """Draw the HUD into `canvas` (BGRA, will be cleared first)."""
    canvas.fill(0)
    if det.width == 0 or det.height == 0:
        h, w = canvas.shape[:2]
    else:
        h, w = det.height, det.width

    color = cfg.accent_bgr + (255,)        # BGRA
    color_rgb = cfg.accent_bgr
    thin = max(1, cfg.line_thickness - 1)

    # --- corner crosshairs ---
    if cfg.show_crosshairs:
        s = int(min(w, h) * 0.04)
        m = int(min(w, h) * 0.04)
        for cx, cy in [(m, m), (w - m, m), (m, h - m), (w - m, h - m)]:
            _draw_crosshair(canvas, cx, cy, s, color, thin)

    # --- pose skeleton ---
    if cfg.show_pose and det.pose is not None:
        pts = det.pose
        for a, b in POSE_CONNECTIONS:
            if a < len(pts) and b < len(pts):
                if pts[a, 2] < 0.3 or pts[b, 2] < 0.3:
                    continue
                p1 = (int(pts[a, 0] * w), int(pts[a, 1] * h))
                p2 = (int(pts[b, 0] * w), int(pts[b, 1] * h))
                cv2.line(canvas, p1, p2, color, thin, cv2.LINE_AA)
        for x, y, v in pts:
            if v < 0.3:
                continue
            cv2.circle(canvas, (int(x * w), int(y * h)), cfg.dot_radius,
                       color, -1, cv2.LINE_AA)

    # --- hands ---
    if cfg.show_hands and det.hands:
        for hand in det.hands:
            for a, b in HAND_CONNECTIONS:
                if a >= len(hand) or b >= len(hand):
                    continue
                p1 = (int(hand[a, 0] * w), int(hand[a, 1] * h))
                p2 = (int(hand[b, 0] * w), int(hand[b, 1] * h))
                cv2.line(canvas, p1, p2, color, thin, cv2.LINE_AA)
            for i, (x, y, _z) in enumerate(hand):
                r = cfg.dot_radius + (2 if i in (4, 8, 12, 16, 20) else 0)
                cv2.circle(canvas, (int(x * w), int(y * h)), r,
                           color, -1, cv2.LINE_AA)

    # --- face mesh dots ---
    if cfg.show_face_mesh and det.face_mesh is not None:
        for x, y in det.face_mesh:
            cv2.circle(canvas, (int(x * w), int(y * h)), 1,
                       color, -1, cv2.LINE_AA)

    # --- faces ---
    for i, f in enumerate(det.faces):
        x1 = int(f.x * w)
        y1 = int(f.y * h)
        x2 = int((f.x + f.w) * w)
        y2 = int((f.y + f.h) * h)
        if cfg.show_face_box:
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color,
                          cfg.line_thickness, cv2.LINE_AA)
            # tactical corner marks
            cs = max(8, int(min(x2 - x1, y2 - y1) * 0.15))
            for (cx, cy, dx, dy) in [
                (x1, y1,  1,  1), (x2, y1, -1,  1),
                (x1, y2,  1, -1), (x2, y2, -1, -1),
            ]:
                cv2.line(canvas, (cx, cy), (cx + dx * cs, cy), color,
                         cfg.line_thickness, cv2.LINE_AA)
                cv2.line(canvas, (cx, cy), (cx, cy + dy * cs), color,
                         cfg.line_thickness, cv2.LINE_AA)
        if cfg.show_face_keypoints:
            for (kx, ky) in f.keypoints:
                cv2.circle(canvas, (int(kx * w), int(ky * h)),
                           cfg.dot_radius + 1, color, -1, cv2.LINE_AA)
        # eye rings + crosshair
        if cfg.show_eyes and f.eyes:
            for (ex, ey, ew, eh) in f.eyes:
                px1, py1 = int(ex * w), int(ey * h)
                px2, py2 = int((ex + ew) * w), int((ey + eh) * h)
                cv2.rectangle(canvas, (px1, py1), (px2, py2),
                              color, thin, cv2.LINE_AA)
                # eye centre crosshair
                ccx = (px1 + px2) // 2
                ccy = (py1 + py2) // 2
                rsz = max(4, (px2 - px1) // 4)
                cv2.line(canvas, (ccx - rsz, ccy), (ccx + rsz, ccy),
                         color, thin, cv2.LINE_AA)
                cv2.line(canvas, (ccx, ccy - rsz), (ccx, ccy + rsz),
                         color, thin, cv2.LINE_AA)
        if cfg.show_pupils and f.pupils:
            for (ux, uy) in f.pupils:
                cv2.circle(canvas, (int(ux * w), int(uy * h)),
                           cfg.dot_radius + 2, color, 2, cv2.LINE_AA)
                cv2.circle(canvas, (int(ux * w), int(uy * h)),
                           1, color, -1, cv2.LINE_AA)
        if cfg.show_vectors and i == 0:
            # lines from face to image corners (top-right + bottom-left)
            fcx, fcy = int((f.x + f.w * 0.5) * w), int((f.y + f.h * 0.5) * h)
            cv2.line(canvas, (fcx, fcy), (w - int(w * 0.03), int(h * 0.06)),
                     color, thin, cv2.LINE_AA)
            cv2.line(canvas, (fcx, fcy), (int(w * 0.03), h - int(h * 0.06)),
                     color, thin, cv2.LINE_AA)
            # inner reticle: small box inside the face box
            rsize = max(6, int(min(f.w, f.h) * w * 0.08))
            cv2.rectangle(canvas, (fcx - rsize//2, fcy - rsize//2),
                          (fcx + rsize//2, fcy + rsize//2),
                          color, thin, cv2.LINE_AA)
        if cfg.show_text:
            face_ctx = {
                "INDEX": f"{i+1:02d}",
                "CONF":  f"{f.confidence:.2f}",
            }
            label = _fmt(cfg.text_face_label, face_ctx)
            cv2.putText(canvas, label, (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, cfg.font_scale,
                        color, thin, cv2.LINE_AA)

    # --- blob tracking ---
    if cfg.show_blobs and det.blobs:
        for i, b in enumerate(det.blobs):
            x1 = int(b.x * w);  y1 = int(b.y * h)
            x2 = int((b.x + b.w) * w);  y2 = int((b.y + b.h) * h)
            cx = int(b.cx * w); cy = int(b.cy * h)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color,
                          cfg.line_thickness, cv2.LINE_AA)
            # crosshair at centroid
            cs = max(6, (x2 - x1) // 8)
            cv2.line(canvas, (cx - cs, cy), (cx + cs, cy), color, thin, cv2.LINE_AA)
            cv2.line(canvas, (cx, cy - cs), (cx, cy + cs), color, thin, cv2.LINE_AA)
            cv2.circle(canvas, (cx, cy), 3, color, -1, cv2.LINE_AA)
            if cfg.show_text:
                blob_ctx = {
                    "INDEX": f"{i+1:02d}",
                    "AREA":  f"{b.area_px}",
                    "X":     f"{cx:04d}",
                    "Y":     f"{cy:04d}",
                    "W":     f"{x2 - x1}",
                    "H":     f"{y2 - y1}",
                }
                label = _fmt(cfg.text_blob_label, blob_ctx)
                cv2.putText(canvas, label, (x1, max(14, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, cfg.font_scale,
                            color, thin, cv2.LINE_AA)

    # --- global HUD text (4 corners — user-editable templates) ---
    if cfg.show_text:
        t = time.monotonic() - _RUN_START
        mins = int(t // 60); secs = int(t % 60); frames = int((t % 1.0) * 30)
        ctx = {
            "TC":      f"{mins:02d}:{secs:02d}:{frames:02d}",
            "FRAME":   f"{frame_idx:08d}",
            "WIDTH":   str(w),
            "HEIGHT":  str(h),
            "FACES":   f"{len(det.faces):02d}",
        }
        tl = _fmt(cfg.text_top_left,     ctx)
        tr = _fmt(cfg.text_top_right,    ctx)
        bl = _fmt(cfg.text_bottom_left,  ctx)
        br = _fmt(cfg.text_bottom_right, ctx)
        # top-left
        cv2.putText(canvas, tl, (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, cfg.font_scale, color, thin, cv2.LINE_AA)
        # top-right (right-anchored)
        (tw_, _th), _ = cv2.getTextSize(tr, cv2.FONT_HERSHEY_SIMPLEX,
                                        cfg.font_scale, thin)
        cv2.putText(canvas, tr, (max(10, w - tw_ - 10), 22),
                    cv2.FONT_HERSHEY_SIMPLEX, cfg.font_scale, color, thin, cv2.LINE_AA)
        # bottom-left
        cv2.putText(canvas, bl, (10, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, cfg.font_scale, color, thin, cv2.LINE_AA)
        # bottom-right (right-anchored)
        (bw_, _bh), _ = cv2.getTextSize(br, cv2.FONT_HERSHEY_SIMPLEX,
                                        cfg.font_scale, thin)
        cv2.putText(canvas, br, (max(10, w - bw_ - 10), h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, cfg.font_scale, color, thin, cv2.LINE_AA)

    # alpha = max channel — anywhere we drew pixels gets opaque
    if canvas.shape[2] == 4:
        canvas[..., 3] = np.maximum(
            np.maximum(canvas[..., 0], canvas[..., 1]), canvas[..., 2])
