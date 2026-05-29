"""Maximalist sticker overlay — chaotic Y2K/dreamcore decorative shapes.

Renders ovals, sparkles, crosses, hearts and frame brackets at random
positions on a BGRA canvas, refreshed periodically. Same plumbing as
overlay.py (CPU-side cv2 draw → texture upload → composite shader).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np


_RUN_START = time.monotonic()
_PALETTE = [
    (255, 64, 32),    # orange-red (BGR)
    (40, 80, 255),    # red
    (255, 128, 32),   # cyan-blue
    (255, 230, 64),   # cyan-ish
    (96, 32, 255),    # magenta-red
    (160, 255, 255),  # pale yellow
    (255, 255, 255),  # white
    (32, 200, 0),     # green
]


@dataclass
class StickerConfig:
    count: int = 40             # how many stickers visible
    refresh_sec: float = 2.0    # how often to re-roll positions
    scale: float = 0.06         # base size relative to min(w, h)
    chaos: float = 1.0          # size & color variance
    seed: int = 0               # static seed; bump for new layout


@dataclass
class _Sticker:
    kind: int            # 0..6
    x: float
    y: float
    size: float
    angle: float
    color: tuple
    thickness: int


def _rand(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(rng.uniform(lo, hi))


def _new_stickers(cfg: StickerConfig) -> List[_Sticker]:
    rng = np.random.default_rng(cfg.seed)
    stickers: List[_Sticker] = []
    for _ in range(cfg.count):
        kind = int(rng.integers(0, 7))
        s = cfg.scale * _rand(rng, 0.4, 1.0 + 2.0 * cfg.chaos)
        stickers.append(_Sticker(
            kind=kind,
            x=_rand(rng, 0.05, 0.95),
            y=_rand(rng, 0.05, 0.95),
            size=s,
            angle=_rand(rng, 0.0, math.pi * 2),
            color=_PALETTE[int(rng.integers(0, len(_PALETTE)))],
            thickness=int(rng.integers(1, 4)),
        ))
    return stickers


def _draw_oval(canvas, cx, cy, w, h, ang, color, thick):
    cv2.ellipse(canvas, (cx, cy), (w, h), math.degrees(ang),
                0, 360, color + (255,), thick, cv2.LINE_AA)


def _draw_filled_oval(canvas, cx, cy, w, h, ang, color):
    cv2.ellipse(canvas, (cx, cy), (w, h), math.degrees(ang),
                0, 360, color + (255,), -1, cv2.LINE_AA)


def _draw_cross(canvas, cx, cy, size, ang, color, thick):
    s = math.sin(ang); c = math.cos(ang)
    p1 = (int(cx + c * size), int(cy + s * size))
    p2 = (int(cx - c * size), int(cy - s * size))
    p3 = (int(cx - s * size * 0.5), int(cy + c * size * 0.5))
    p4 = (int(cx + s * size * 0.5), int(cy - c * size * 0.5))
    cv2.line(canvas, p1, p2, color + (255,), thick, cv2.LINE_AA)
    cv2.line(canvas, p3, p4, color + (255,), thick, cv2.LINE_AA)


def _draw_heart(canvas, cx, cy, size, color):
    pts = []
    for i in range(40):
        t = i / 40.0 * math.pi * 2
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2*t) - 2 * math.cos(3*t) - math.cos(4*t))
        pts.append((int(cx + x * size / 16), int(cy + y * size / 16)))
    pts_arr = np.array(pts, dtype=np.int32)
    cv2.fillPoly(canvas, [pts_arr], color + (255,), cv2.LINE_AA)


def _draw_sparkle(canvas, cx, cy, size, ang, color, thick):
    for k in range(4):
        a = ang + k * math.pi / 4
        lx = math.cos(a) * size
        ly = math.sin(a) * size
        cv2.line(canvas, (int(cx - lx), int(cy - ly)),
                 (int(cx + lx), int(cy + ly)), color + (255,), thick, cv2.LINE_AA)


def _draw_frame(canvas, cx, cy, size, color, thick):
    s = int(size)
    cv2.rectangle(canvas, (cx - s, cy - s), (cx + s, cy + s),
                  color + (255,), thick, cv2.LINE_AA)
    # bracket marks at corners
    b = max(3, s // 3)
    for ox, oy in [(-s, -s), (s, -s), (-s, s), (s, s)]:
        cv2.circle(canvas, (cx + ox, cy + oy), b, color + (255,),
                   thick, cv2.LINE_AA)


def _draw_arrow(canvas, cx, cy, size, ang, color, thick):
    ex = int(cx + math.cos(ang) * size)
    ey = int(cy + math.sin(ang) * size)
    cv2.arrowedLine(canvas, (cx, cy), (ex, ey), color + (255,),
                    thick, cv2.LINE_AA, tipLength=0.4)


def _draw_ring(canvas, cx, cy, size, color, thick):
    cv2.circle(canvas, (cx, cy), int(size), color + (255,), thick, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), int(size * 0.6), color + (255,),
               max(1, thick - 1), cv2.LINE_AA)


class StickerLayer:
    """Maintains a current set of stickers, refreshed on a timer."""

    def __init__(self) -> None:
        self._cfg = StickerConfig()
        self._stickers: List[_Sticker] = []
        self._last_refresh = 0.0
        self._cached_seed = -1

    def render(self, canvas: np.ndarray, cfg: StickerConfig) -> None:
        """Draw the current stickers into the canvas (must already be cleared by caller)."""
        # refresh on timer or seed change
        now = time.monotonic()
        if (now - self._last_refresh > cfg.refresh_sec
                or self._cached_seed != cfg.seed
                or len(self._stickers) != cfg.count):
            # use wall-clock-derived seed for variety, unless user pinned one
            cfg2 = StickerConfig(**cfg.__dict__)
            if cfg2.seed == 0:
                cfg2.seed = int(now * 1000) & 0xFFFF
            self._stickers = _new_stickers(cfg2)
            self._last_refresh = now
            self._cached_seed = cfg.seed

        h, w = canvas.shape[:2]
        base = int(min(w, h))
        for s in self._stickers:
            cx = int(s.x * w); cy = int(s.y * h)
            size = max(4, int(s.size * base))
            color = s.color
            try:
                if s.kind == 0:
                    _draw_oval(canvas, cx, cy, size, size//2, s.angle, color, s.thickness)
                elif s.kind == 1:
                    _draw_filled_oval(canvas, cx, cy, size, size//2, s.angle, color)
                elif s.kind == 2:
                    _draw_cross(canvas, cx, cy, size, s.angle, color, s.thickness + 1)
                elif s.kind == 3:
                    _draw_heart(canvas, cx, cy, size, color)
                elif s.kind == 4:
                    _draw_sparkle(canvas, cx, cy, size, s.angle, color, s.thickness)
                elif s.kind == 5:
                    _draw_frame(canvas, cx, cy, size, color, s.thickness)
                elif s.kind == 6:
                    _draw_ring(canvas, cx, cy, size, color, s.thickness + 1)
            except cv2.error:
                pass

        # rebuild alpha
        if canvas.shape[2] == 4:
            canvas[..., 3] = np.maximum(
                np.maximum(canvas[..., 0], canvas[..., 1]), canvas[..., 2])
