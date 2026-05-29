"""ASCII glyph atlas generator.

Bakes 8 glyphs (sorted from empty → solid) into a single grayscale strip image
so the ASCII shader can pick the right glyph by sampling a column from it.

  glyph 0: ' '   (full empty)
  glyph 1: '.'
  glyph 2: ':'
  glyph 3: '+'
  glyph 4: '='
  glyph 5: '#'
  glyph 6: '@'
  glyph 7: '█'   (solid)

Cell is 16×16 px. Atlas size: 128×16 px (8 glyphs × 16 px wide).
Stored as 1-channel PNG. Generated once into models/ascii_atlas.png and cached.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


GLYPHS = " .:+=#@█"     # last one is FULL BLOCK; user-overridable in GUI
CELL = 16
MAX_GLYPHS = 32          # safety: too many slows the shader and crowds the bar


def _try_font():
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\cour.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, CELL)
            except Exception:
                continue
    try:
        from PIL import ImageFont
        return ImageFont.load_default()
    except Exception:
        return None


def _bake_with_pil(out_path: Path, glyphs: str = GLYPHS) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    font = _try_font()
    if font is None:
        return False
    if not glyphs:
        glyphs = GLYPHS
    glyphs = glyphs[:MAX_GLYPHS]
    W = CELL * len(glyphs)
    img = Image.new("L", (W, CELL), 0)
    draw = ImageDraw.Draw(img)
    for i, ch in enumerate(glyphs):
        try:
            bbox = draw.textbbox((0, 0), ch, font=font)
            gw = bbox[2] - bbox[0]
            gh = bbox[3] - bbox[1]
            x = i * CELL + (CELL - gw) // 2 - bbox[0]
            y = (CELL - gh) // 2 - bbox[1]
            draw.text((x, y), ch, fill=255, font=font)
        except Exception:
            draw.text((i * CELL + 2, 0), ch, fill=255, font=font)
    img.save(out_path)
    return True


def _bake_procedural(out_path: Path) -> None:
    """No-PIL fallback: hand-painted 16x16 cells for each glyph."""
    W = CELL * len(GLYPHS)
    arr = np.zeros((CELL, W), dtype=np.uint8)
    # glyph 0 = empty
    # glyph 1 = '.' — small dot near bottom
    arr[12:14, 1*CELL + 7:1*CELL + 9] = 255
    # glyph 2 = ':' — two dots
    arr[5:7,  2*CELL + 7:2*CELL + 9] = 255
    arr[11:13,2*CELL + 7:2*CELL + 9] = 255
    # glyph 3 = '+'
    arr[3:13, 3*CELL + 7:3*CELL + 9] = 255
    arr[7:9,  3*CELL + 3:3*CELL + 13] = 255
    # glyph 4 = '=' — two horizontal bars
    arr[5:7,  4*CELL + 3:4*CELL + 13] = 255
    arr[9:11, 4*CELL + 3:4*CELL + 13] = 255
    # glyph 5 = '#' — hash
    arr[3:13, 5*CELL + 5:5*CELL + 6] = 255
    arr[3:13, 5*CELL + 9:5*CELL + 10] = 255
    arr[5:6,  5*CELL + 3:5*CELL + 13] = 255
    arr[9:10, 5*CELL + 3:5*CELL + 13] = 255
    # glyph 6 = '@' — densely-filled box
    arr[2:14, 6*CELL + 2:6*CELL + 14] = 200
    arr[5:11, 6*CELL + 5:6*CELL + 11] = 0
    arr[7:9,  6*CELL + 7:6*CELL + 9] = 255
    # glyph 7 = '█' — solid block
    arr[:, 7*CELL:8*CELL] = 255

    try:
        from PIL import Image
        Image.fromarray(arr, mode="L").save(out_path)
    except ImportError:
        # save as a tiny raw .npy so the renderer can still load it
        np.save(str(out_path) + ".npy", arr)


def make_atlas(out_path: Path, glyphs: str | None = None) -> bool:
    """Bake an atlas. If glyphs is None or empty, the default GLYPHS set is used."""
    out_path.parent.mkdir(exist_ok=True)
    text = (glyphs or GLYPHS)[:MAX_GLYPHS]
    if _bake_with_pil(out_path, text):
        return True
    _bake_procedural(out_path)
    return out_path.exists()


def make_atlas_array(glyphs: str) -> tuple[np.ndarray, int, int]:
    """Bake an atlas in-memory (no file IO). Returns (ndarray, count, cell_px).
    Used by the renderer to live-update the texture when the user edits the
    ASCII glyph string in the GUI."""
    text = (glyphs or GLYPHS)[:MAX_GLYPHS]
    text = text or " "
    count = len(text)
    W = CELL * count
    arr = np.zeros((CELL, W), dtype=np.uint8)
    try:
        from PIL import Image, ImageDraw
        font = _try_font()
        if font is not None:
            img = Image.new("L", (W, CELL), 0)
            draw = ImageDraw.Draw(img)
            for i, ch in enumerate(text):
                try:
                    bbox = draw.textbbox((0, 0), ch, font=font)
                    gw = bbox[2] - bbox[0]
                    gh = bbox[3] - bbox[1]
                    x = i * CELL + (CELL - gw) // 2 - bbox[0]
                    y = (CELL - gh) // 2 - bbox[1]
                    draw.text((x, y), ch, fill=255, font=font)
                except Exception:
                    draw.text((i * CELL + 2, 0), ch, fill=255, font=font)
            arr = np.array(img, dtype=np.uint8)
    except ImportError:
        pass
    return arr, count, CELL


def load_atlas(path: Path):
    """Returns (np.ndarray uint8 shape (H, W), glyph_count, cell_px)."""
    try:
        from PIL import Image
        img = Image.open(path).convert("L")
        arr = np.array(img, dtype=np.uint8)
    except Exception:
        arr = np.load(str(path) + ".npy")
    H, W = arr.shape
    return arr, len(GLYPHS), W // len(GLYPHS)
