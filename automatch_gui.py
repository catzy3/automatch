"""AutoMatch GUI — redesigned with proper dark-pro theme and effects table.

Effects panel is the star: cycling state buttons with per-state colour themes,
always-visible mix sliders, proper table alignment.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import dearpygui.dearpygui as dpg

from copy import deepcopy
from dataclasses import replace as dataclass_replace
from automatch import (
    AutoEditSettings, PALETTES, USER_EFFECTS, EFFECT_LABELS_RU,
    USER_EFFECTS_NO_MIX,
    TextCardSettings, VIBE_TEXTS, CLIP_MODES,
    BlobTrackSettings,
    audio_duration,
    preview as automatch_preview, run as automatch_run,
)
from automatch_mod import LFO_WAVES, MOD_SOURCES, COMBINE_MODES, MOD_MODES, LFO_NAMES, BEAT_SPIKE_PRESET, DOWNBEAT_PULSE_PRESET

# ── short labels for the compact effects table ───────────────────────────────
_FX_SHORT = {
    "luma_key":      "Luma Key",
    "duotone":       "Duotone",
    "dual_tone":     "Dual Tone",
    "heavy_grain":   "Grain",
    "motion_trails": "Trails",
    "wave_warp":     "Wave Warp",
    "pixel_sort":    "Pixel Sort",
    "datamosh":      "Datamosh",
    "vhs":           "VHS",
    "ascii_mosaic":  "ASCII",
    "crt":           "CRT",
    "dither":        "Dither",
    "kaleidoscope":         "Kaleido",
    "slit_scan":            "Slit-scan",
    "found_footage":        "Found Footage",
    "chromatic_aberration": "Chr. Aberration",
    "color_posterize":      "Posterize",
    "swirl":                "Swirl",
    "iridescent_foil":      "Iridescent Foil",
    "motion_blur":          "Motion Blur",
    "advanced_glitch":      "Advanced Glitch",
}

# Set by the entry point (shi_o_kiku.py) to the directory where the user
# wants writable files (recordings, last_session.json, presets) to live.
# Falls back to the script directory in dev mode.
APP_NAME: str = ""
APP_VERSION: str = ""
APP_DIR: Path | None = None

# ─── Render cancellation + auto-numbered output ──────────────────────────
# Shared cancel signal — set from the Stop button, polled by the render
# loop inside automatch.run().
_render_cancel: threading.Event = threading.Event()


def _next_indexed_path(base: Path) -> Path:
    """Return an output path that does NOT already exist.

    Strategy: if ``base`` itself is free → use it.  Otherwise append the
    smallest integer suffix that yields a free filename:
        base.mp4   exists → base_1.mp4
        base_1.mp4 also exists → base_2.mp4
    Keeps your previous renders intact instead of overwriting.
    """
    base = Path(base)
    if not base.exists():
        return base
    parent = base.parent
    stem = base.stem
    suf  = base.suffix if base.suffix else ".mp4"
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suf}"
        if not candidate.exists():
            return candidate
        n += 1
        if n > 9999:                              # paranoid stop
            return parent / f"{stem}_{int(time.time())}{suf}"


def _cancel_render(_s=None, _a=None):
    """Stop button callback — flips the cancel event."""
    _render_cancel.set()
    try:
        dpg.set_value("status", "Останавливаю рендер…")
    except Exception:
        pass
    # disable the stop button so it can't be hit twice
    if dpg.does_item_exist("stop_btn"):
        dpg.configure_item("stop_btn", enabled=False, label="Останавливаю…")

# ── global GUI state ──────────────────────────────────────────────────────────
STATE = {
    "audio":          "",
    "media":          "",
    "output":         str(Path(__file__).parent / "recordings" / "automatch.mp4"),
    "audio_duration": 0.0,
}

# per-effect state lives here (not in DPG widget value)
_FX_STATE: dict[str, str] = {k: "off" for k in USER_EFFECTS}

# per-effect compositing mode (TouchDesigner-style Composite TOP). Index into
# _BLEND_MODES; maps to EffectConfig.blend. 0 = cross/over = legacy behaviour.
_FX_BLEND: dict[str, int] = {k: 0 for k in USER_EFFECTS}
# Order MUST match EffectConfig.blend / shader _blendOp() indices.
_BLEND_MODES = ("Cross (норм.)", "Add", "Screen", "Multiply", "Overlay",
                "Difference", "Lighten", "Darken", "Dodge", "Burn", "Subtract")
# Effects whose shader actually honours u_blend (experiment scope: FM + CRT).
_BLEND_FX = ("crt", "crt_monitor")

# which effect is currently focused in the side params panel
_SELECTED_FX: str = ""

# theme handles — populated after dpg.setup_dearpygui()
_BTN_THEMES:     dict[str, int] = {}   # state → theme tag
_BTN_THEMES_SEL: dict[str, int] = {}   # state → theme (selected variant)
_SLIDER_ACTIVE:  int = 0              # theme for enabled sliders
_SLIDER_DIMMED:  int = 0              # theme for disabled sliders


# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_time(t: float) -> str:
    if t is None or t < 0:
        return "--:--"
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _refresh_offset_label():
    dur      = STATE.get("audio_duration", 0.0) or 0.0
    off      = dpg.get_value("offset")    if dpg.does_item_exist("offset")   else 0.0
    edit_len = dpg.get_value("duration")  if dpg.does_item_exist("duration") else 13.0
    end      = min(dur, off + edit_len)   if dur > 0 else off + edit_len
    if dur > 0:
        txt = f"{_fmt_time(off)} → {_fmt_time(end)}  (трек {_fmt_time(dur)})"
    else:
        txt = f"{_fmt_time(off)} → {_fmt_time(off + edit_len)}"
    if dpg.does_item_exist("offset_label"):
        dpg.set_value("offset_label", txt)


# ═══════════════════════════════════════════════════════════════════════════════
# native file dialogs (tkinter)
# ═══════════════════════════════════════════════════════════════════════════════

def _tk_dialog(fn):
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return fn(root) or ""
        finally:
            root.destroy()
    except ImportError:
        return ""


def _ask_audio() -> str:
    from tkinter import filedialog
    return _tk_dialog(lambda r: filedialog.askopenfilename(
        title="Выбери аудио",
        filetypes=[("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac"), ("Все", "*.*")],
    ))


def _ask_media_folder() -> str:
    from tkinter import filedialog
    return _tk_dialog(lambda r: filedialog.askdirectory(title="Папка с media"))


def _ask_single_video() -> str:
    from tkinter import filedialog
    return _tk_dialog(lambda r: filedialog.askopenfilename(
        title="Один видео-файл для эдита",
        filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"),
                   ("All", "*.*")],
    ))


def _ask_output_path() -> str:
    from tkinter import filedialog
    return _tk_dialog(lambda r: filedialog.asksaveasfilename(
        title="Сохранить mp4",
        defaultextension=".mp4", initialfile="automatch.mp4",
        filetypes=[("MP4", "*.mp4")],
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# file-picker callbacks
# ═══════════════════════════════════════════════════════════════════════════════

def _pick_audio(_s=None, _a=None):
    path = _ask_audio()
    if not path:
        return
    STATE["audio"] = path
    dur = audio_duration(Path(path))
    STATE["audio_duration"] = float(dur) if dur else 0.0
    name = Path(path).name
    label = f"{name}   [{_fmt_time(dur)}]" if dur else f"{name}   [длина неизвестна]"
    dpg.set_value("audio_label", label)
    if dur and dpg.does_item_exist("offset"):
        edit_len = dpg.get_value("duration") if dpg.does_item_exist("duration") else 13.0
        dpg.configure_item("offset", max_value=max(0.0, float(dur) - max(0.5, edit_len)))
    _refresh_offset_label()


_SINGLE_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def _refresh_media_label():
    """Set the media label + add a [одно видео] tag when the path is a file."""
    p = STATE.get("media", "")
    if not p:
        if dpg.does_item_exist("media_label"):
            dpg.set_value("media_label", "")
        return
    label = _truncate(p, 55)
    pp = Path(p)
    # Use ASCII-safe icons covered by our font's character ranges.
    if pp.is_file() and pp.suffix.lower() in _SINGLE_VIDEO_EXTS:
        label = "[V] " + label + "   [одно видео]"
    elif pp.is_dir():
        label = "[D] " + label
    if dpg.does_item_exist("media_label"):
        dpg.set_value("media_label", label)


def _pick_media(_s=None, _a=None):
    path = _ask_media_folder()
    if path:
        STATE["media"] = path
        _refresh_media_label()


def _pick_single_video(_s=None, _a=None):
    path = _ask_single_video()
    if path:
        STATE["media"] = path
        _refresh_media_label()


def _pick_output(_s=None, _a=None):
    path = _ask_output_path()
    if path:
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        STATE["output"] = path
        dpg.set_value("output_label", _truncate(path, 55))


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else "…" + s[-(n - 1):]


# ═══════════════════════════════════════════════════════════════════════════════
# offset / duration sync
# ═══════════════════════════════════════════════════════════════════════════════

def _on_offset_change(_s, v):
    if dpg.does_item_exist("offset_exact"):
        dpg.set_value("offset_exact", float(v))
    _refresh_offset_label()


def _on_duration_change(_s, _v):
    dur = STATE.get("audio_duration", 0.0) or 0.0
    edit_len = dpg.get_value("duration")
    if dur and dpg.does_item_exist("offset"):
        max_off = max(0.0, dur - max(0.5, edit_len))
        dpg.configure_item("offset", max_value=max_off)
        if dpg.get_value("offset") > max_off:
            dpg.set_value("offset", max_off)
    _refresh_offset_label()


# ═══════════════════════════════════════════════════════════════════════════════
# effects state machine
# ═══════════════════════════════════════════════════════════════════════════════

_STATE_CYCLE  = ["off", "on", "random", "locked"]
_STATE_LABELS = {
    "off":    "OFF",
    "on":     "ON",
    "random": "RND",
    "locked": "LCK",
}

# short abbreviations for the GL1TCH-style matrix cells
_FX_ABBR = {
    "luma_key":      "LUMA",
    "duotone":       "DUO",
    "dual_tone":     "DUAL",
    "heavy_grain":   "GRAIN",
    "motion_trails": "TRAIL",
    "wave_warp":     "WAVE",
    "pixel_sort":    "PSORT",
    "datamosh":      "DMSH",
    "vhs":           "VHS",
    "ascii_mosaic":  "ASCII",
    "crt":           "CRT",
    "dither":        "DTHR",
    "kaleidoscope":         "KALEI",
    "slit_scan":            "SLIT",
    "found_footage":        "FOUND",
    "chromatic_aberration": "CHRAB",
    "color_posterize":      "POST",
    "swirl":                "SWIRL",
    "iridescent_foil":     "IRDSC",
    "motion_blur":         "MBLUR",
    "advanced_glitch":     "AGLCH",
}

# ── browser categories ───────────────────────────────────────────────────────
_FX_CATEGORIES = {
    "АНАЛОГ":   ["vhs", "crt", "crt_monitor", "heavy_grain", "found_footage"],
    "ГЛИТЧ":    ["datamosh", "pixel_sort", "rgb_split", "chromatic_aberration", "advanced_glitch"],
    "ЦВЕТ":     ["duotone", "luma_key", "color_posterize", "iridescent_foil"],
    "ДИСТОРТ":  ["wave_warp", "swirl", "distort", "elastic_grid", "motion_blur"],
    "ГЕНЕР.":   ["dither", "cellular", "ascii_mosaic", "kaleidoscope", "thermal"],
    "ДВИЖЕНИЕ": ["motion_trails", "slit_scan", "edges", "pixel_swap"],
}

# GL1TCH palette (effects panel only; rest of GUI stays deep-navy)
_GL_BG       = (8,   8,  12)        # near-black
_GL_PANEL    = (16, 16,  22)        # panel slightly lifted
_GL_FRAME    = (24, 24,  32)        # interactive frame
_GL_GREEN    = (164, 255,  90)
_GL_MAGENTA  = (255,  34, 153)
_GL_CYAN     = (  0, 216, 212)
_GL_RED      = (221,  51,  71)
_GL_TEXT     = (220, 224, 232)
_GL_DIM      = (110, 120, 138)


def _build_btn_themes():
    """Create per-state button themes. Call after dpg.setup_dearpygui()."""
    global _SLIDER_ACTIVE, _SLIDER_DIMMED

    # bg / hov / act / txt / border for the 4 cell states
    specs = {
        "off":    dict(bg=(28, 28,  36), hov=(42, 42,  52), act=(52, 52,  66),
                       txt=(110, 120, 138), bd=(60,  62,  78)),
        "on":     dict(bg=(18, 72,  30), hov=(26, 96,  40), act=(34,116,  50),
                       txt=_GL_GREEN,      bd=_GL_GREEN),
        "random": dict(bg=(82, 14,  56), hov=(110,18,  74), act=(140,22,  94),
                       txt=_GL_MAGENTA,    bd=_GL_MAGENTA),
        "locked": dict(bg=(8,  64,  62), hov=(12, 86,  82), act=(16,108, 104),
                       txt=_GL_CYAN,       bd=_GL_CYAN),
    }
    for state, c in specs.items():
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,        c["bg"])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, c["hov"])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  c["act"])
                dpg.add_theme_color(dpg.mvThemeCol_Text,          c["txt"])
                dpg.add_theme_color(dpg.mvThemeCol_Border,        c["bd"])
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding,  6, 6)
        _BTN_THEMES[state] = t

        # selected variant — thicker bright border on the same colours
        with dpg.theme() as ts:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,        c["hov"])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, c["act"])
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  c["act"])
                dpg.add_theme_color(dpg.mvThemeCol_Text,          c["txt"])
                dpg.add_theme_color(dpg.mvThemeCol_Border,        _GL_GREEN)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0)
                dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 2)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding,  6, 6)
        _BTN_THEMES_SEL[state] = ts

    with dpg.theme() as ta:
        with dpg.theme_component(dpg.mvSliderFloat):
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab,    (0, 185, 170))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,       (30, 30, 42))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,(40, 40, 55))
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (210, 215, 220))
    _SLIDER_ACTIVE = ta

    with dpg.theme() as td:
        with dpg.theme_component(dpg.mvSliderFloat):
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab,    (55, 55, 65))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,       (28, 28, 36))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,(28, 28, 36))
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (75, 75, 90))
    _SLIDER_DIMMED = td


def _set_fx_state(kind: str, state: str):
    _FX_STATE[kind] = state
    btn = f"fx_btn_{kind}"
    mix = f"fx_mix_{kind}"
    rnd = f"fx_randmix_{kind}"
    if dpg.does_item_exist(btn):
        dpg.configure_item(btn, label=_STATE_LABELS[state])
        if _BTN_THEMES:
            sel = (kind == _SELECTED_FX)
            theme = (_BTN_THEMES_SEL if sel else _BTN_THEMES).get(state)
            if theme:
                dpg.bind_item_theme(btn, theme)
    # mix is editable for any "live" state (on/random/locked).
    # The dither special-case (no per-effect mix slider) is handled inside the
    # side panel.
    active = state in ("on", "random", "locked")
    if dpg.does_item_exist(mix):
        dpg.configure_item(mix, enabled=active)
        if _SLIDER_ACTIVE and _SLIDER_DIMMED:
            dpg.bind_item_theme(mix, _SLIDER_ACTIVE if active else _SLIDER_DIMMED)
    if dpg.does_item_exist(rnd):
        dpg.configure_item(rnd, enabled=active)
    _refresh_browser()


def _cycle_fx_state(_s, _a, kind: str):
    # First click on a cell only OPENS its menu (focuses it in the side
    # panel) — the state is left untouched.  Only a click on the already-
    # selected cell cycles OFF → ON → RND → LCK → OFF.
    if _SELECTED_FX != kind:
        _select_fx(kind)
        return
    cur = _FX_STATE.get(kind, "off")
    if cur not in _STATE_CYCLE:                # legacy "auto" or unknown
        cur = "off"
    nxt = _STATE_CYCLE[(_STATE_CYCLE.index(cur) + 1) % len(_STATE_CYCLE)]
    _set_fx_state(kind, nxt)
    _refresh_side_panel()


def _select_fx(kind: str):
    """Make `kind` the focused cell in the side panel."""
    global _SELECTED_FX
    prev = _SELECTED_FX
    _SELECTED_FX = kind
    # rebind themes on old + new selection so the highlight moves
    for k in (prev, kind):
        if not k:
            continue
        btn = f"fx_btn_{k}"
        if dpg.does_item_exist(btn) and _BTN_THEMES:
            st = _FX_STATE.get(k, "off")
            sel = (k == _SELECTED_FX)
            theme = (_BTN_THEMES_SEL if sel else _BTN_THEMES).get(st)
            if theme:
                dpg.bind_item_theme(btn, theme)
    _refresh_side_panel()


def _randomize_all_effects(_s=None, _a=None):
    """Turn every NON-locked effect into RND with a fresh mix.  Locked cells
    keep their state and mix value untouched."""
    for kind in USER_EFFECTS:
        if _FX_STATE.get(kind) == "locked":
            continue
        _set_fx_state(kind, "random")
        mix = f"fx_mix_{kind}"
        if dpg.does_item_exist(mix):
            dpg.set_value(mix, round(random.uniform(0.35, 0.85), 2))
        rnd = f"fx_randmix_{kind}"
        if dpg.does_item_exist(rnd):
            dpg.set_value(rnd, True)
    _refresh_side_panel()
    if dpg.does_item_exist("status"):
        dpg.set_value("status", "Все эффекты → RND (locked не тронуты)")


def _reset_all_effects(_s=None, _a=None):
    """Clear matrix — set every NON-locked effect to OFF."""
    for kind in USER_EFFECTS:
        if _FX_STATE.get(kind) == "locked":
            continue
        _set_fx_state(kind, "off")
    _refresh_side_panel()
    if dpg.does_item_exist("status"):
        dpg.set_value("status", "Все эффекты → OFF (locked не тронуты)")


def _kill_all_effects(_s=None, _a=None):
    """Kill ALL effects including locked ones."""
    for kind in USER_EFFECTS:
        _set_fx_state(kind, "off")
    _refresh_side_panel()
    if dpg.does_item_exist("status"):
        dpg.set_value("status", "KILL ALL — все эффекты выключены")


_BROWSER_SELECTED: str = ""       # currently previewed effect
_preview_queue: "queue.Queue" = None  # filled on first use


def _get_preview_queue():
    global _preview_queue
    if _preview_queue is None:
        import queue as _q
        _preview_queue = _q.Queue()
    return _preview_queue


def _refresh_browser():
    """Sync browser button labels and themes with current _FX_STATE."""
    for kind in USER_EFFECTS:
        tag = f"br_btn_{kind}"
        if not dpg.does_item_exist(tag):
            continue
        state = _FX_STATE.get(kind, "off")
        sel   = (kind == _BROWSER_SELECTED)
        theme = (_BTN_THEMES_SEL if sel else _BTN_THEMES).get(state)
        if theme:
            dpg.bind_item_theme(tag, theme)
        abbr = _FX_ABBR.get(kind, kind[:5].upper())
        dot  = {"off": "OFF", "on": "ON ", "random": "RND", "locked": "LCK"}.get(state, "OFF")
        dpg.configure_item(tag, label=f"{abbr:<6} ●{dot}")


def _browser_select(kind: str):
    """Click handler: cycle state AND trigger preview generation."""
    global _BROWSER_SELECTED
    _cycle_fx_state(None, None, kind)
    _BROWSER_SELECTED = kind
    _refresh_browser()
    _browser_show_preview(kind)


def _browser_show_preview(kind: str):
    """Kick off background preview generation for kind."""
    if not dpg.does_item_exist("br_preview_label"):
        return
    dpg.configure_item("br_preview_label",
                       default_value=f"Генерирую {_FX_ABBR.get(kind, kind)}…")
    q = _get_preview_queue()

    def _gen():
        try:
            from effect_preview import generate_preview
            arr = generate_preview(kind, 200, 130)
            if arr is not None:
                q.put((kind, arr.flatten().tolist()))
        except Exception:
            pass

    threading.Thread(target=_gen, daemon=True).start()


def _drain_preview_queue():
    """Called every 6 frames from main thread to upload pending previews."""
    q = _get_preview_queue()
    while not q.empty():
        try:
            kind, flat = q.get_nowait()
            if dpg.does_item_exist("br_preview_tex"):
                dpg.set_value("br_preview_tex", flat)
            if dpg.does_item_exist("br_preview_label"):
                dpg.configure_item("br_preview_label",
                                   default_value=EFFECT_LABELS_RU.get(kind, kind))
        except Exception:
            pass
    # reschedule every 6 frames (~10Hz at 60fps)
    dpg.set_frame_callback(dpg.get_frame_count() + 6, _drain_preview_queue)


def _browser_filter(_s=None, _a=None, _u=None):
    """Show/hide browser rows based on search input."""
    if not dpg.does_item_exist("br_search"):
        return
    query = dpg.get_value("br_search").strip().lower()
    for kind in USER_EFFECTS:
        row_tag = f"br_row_{kind}"
        if not dpg.does_item_exist(row_tag):
            continue
        visible = (not query
                   or query in kind.lower()
                   or query in _FX_ABBR.get(kind, "").lower()
                   or query in _FX_SHORT.get(kind, "").lower())
        dpg.configure_item(row_tag, show=visible)
    # hide category headers when all children are hidden
    for cat, members in _FX_CATEGORIES.items():
        hdr_tag = f"br_hdr_{cat}"
        if not dpg.does_item_exist(hdr_tag):
            continue
        any_visible = any(
            dpg.does_item_exist(f"br_row_{k}") and dpg.is_item_shown(f"br_row_{k}")
            for k in members if k in USER_EFFECTS
        )
        dpg.configure_item(hdr_tag, show=any_visible)


def _build_effect_browser_window():
    """Floating effect browser — call once after dpg.setup_dearpygui().

    Layout:
        ┌──────────── left: effect list ──────────┐ ┌── right: preview ──┐
        │  [Поиск...]              [CLEAR]         │ │  [200×130 preview] │
        │  ─── АНАЛОГ ────────────────────────── │ │  Effect name       │
        │  [VHS ●OFF] [CRT ●OFF] [CRTM ●RND]     │ │  [Description]     │
        │  ...                                    │ │                    │
        └─────────────────────────────────────────┘ └────────────────────┘
    """
    # Placeholder preview: grey gradient 200×130
    _prev_w, _prev_h = 200, 130
    placeholder = [0.1, 0.1, 0.15, 1.0] * (_prev_w * _prev_h)

    with dpg.texture_registry(tag="br_tex_reg"):
        dpg.add_dynamic_texture(width=_prev_w, height=_prev_h,
                                default_value=placeholder,
                                tag="br_preview_tex",
                                parent="br_tex_reg")

    with dpg.theme() as _win_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,      _GL_BG + (255,))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (28, 10, 38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,       (20,  8, 28, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border,        _GL_MAGENTA + (180,))
            dpg.add_theme_color(dpg.mvThemeCol_Text,          _GL_TEXT)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 1)
    with dpg.theme() as _search_theme:
        with dpg.theme_component(dpg.mvInputText):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        (22, 22, 30))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (30, 30, 42))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           _GL_CYAN)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,  0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)

    with dpg.window(label="EFFECT BROWSER", tag="fx_browser_win",
                    width=720, height=620, show=False, no_close=False,
                    pos=(160, 80)):
        dpg.bind_item_theme("fx_browser_win", _win_theme)

        with dpg.group(horizontal=True):
            # ── LEFT: search + effect list ──────────────────────────────
            with dpg.group(width=430):
                with dpg.group(horizontal=True):
                    src = dpg.add_input_text(tag="br_search", hint="Поиск...",
                                             width=330, callback=_browser_filter,
                                             on_enter=False)
                    dpg.bind_item_theme(src, _search_theme)
                    dpg.add_button(label="CLR", width=50, height=24,
                                   callback=lambda: (dpg.set_value("br_search", ""),
                                                     _browser_filter()))
                dpg.add_spacer(height=4)
                dpg.add_separator()

                with dpg.child_window(width=428, height=-1, border=False):
                    for cat_label, members in _FX_CATEGORIES.items():
                        valid = [k for k in members if k in USER_EFFECTS]
                        if not valid:
                            continue
                        dpg.add_spacer(height=4)
                        dpg.add_text(f"── {cat_label} ──",
                                     tag=f"br_hdr_{cat_label}",
                                     color=_GL_MAGENTA)
                        dpg.add_spacer(height=2)
                        for i, kind in enumerate(valid):
                            abbr  = _FX_ABBR.get(kind, kind[:5].upper())
                            state = _FX_STATE.get(kind, "off")
                            dot   = {"off":"OFF","on":"ON ","random":"RND","locked":"LCK"}.get(state,"OFF")
                            with dpg.group(tag=f"br_row_{kind}", horizontal=True):
                                btn = dpg.add_button(
                                    label=f"{abbr:<6} ●{dot}",
                                    tag=f"br_btn_{kind}",
                                    width=130, height=32,
                                    callback=lambda s, a, k=kind: _browser_select(k),
                                )
                                theme = _BTN_THEMES.get(state)
                                if theme:
                                    dpg.bind_item_theme(btn, theme)
                                dpg.add_text(
                                    _FX_SHORT.get(kind, kind),
                                    color=_GL_DIM,
                                )
                            if i % 3 == 2:
                                dpg.add_spacer(height=1)

            # ── RIGHT: preview panel ────────────────────────────────────
            dpg.add_spacer(width=8)
            with dpg.group(width=240):
                dpg.add_text("PREVIEW", color=_GL_CYAN)
                dpg.add_separator()
                dpg.add_spacer(height=4)
                dpg.add_image("br_preview_tex",
                              width=_prev_w, height=_prev_h)
                dpg.add_spacer(height=6)
                dpg.add_text("— кликни эффект —",
                             tag="br_preview_label",
                             color=_GL_DIM, wrap=220)
                dpg.add_spacer(height=8)
                dpg.add_separator()
                dpg.add_spacer(height=4)
                dpg.add_text(
                    "Превью рендерится через\nшейдер на синтетическом\nтестовом кадре.",
                    color=_GL_DIM, wrap=220,
                )


def _collect_effect_overrides() -> dict:
    """Emit overrides for every effect.  `locked` is reported downstream as
    `on` (same render behaviour) — locked is a UI-only sticky flag.
    When bypass_effects_chk is checked all effects are reported as off."""
    overrides = {}
    for kind in USER_EFFECTS:
        state = _FX_STATE.get(kind, "off")
        if state not in _STATE_CYCLE:
            state = "off"
        mix_tag = f"fx_mix_{kind}"
        rnd_tag = f"fx_randmix_{kind}"
        mix_val = float(dpg.get_value(mix_tag)) if dpg.does_item_exist(mix_tag) else 0.65
        rnd_val = bool(dpg.get_value(rnd_tag))  if dpg.does_item_exist(rnd_tag) else False
        out_state = "on" if state == "locked" else state
        overrides[kind] = {"state": out_state, "mix": mix_val,
                           "random_mix": rnd_val,
                           "blend": float(_FX_BLEND.get(kind, 0))}
    return overrides


def _refresh_side_panel():
    """Repaint the side panel (selected name + dither sub-panel visibility).

    Wired to selection / state changes.  Safe to call before widgets exist —
    `does_item_exist` guards everything.
    """
    name_tag = "fx_side_name"
    mix_tag  = "fx_side_mix"
    rnd_tag  = "fx_side_rnd"
    hint_tag = "fx_side_hint"
    dith_tag = "fx_side_dither"

    kind = _SELECTED_FX
    if not kind:
        if dpg.does_item_exist(name_tag):
            dpg.configure_item(name_tag, default_value="— ничего не выбрано —",
                               color=_GL_DIM)
        if dpg.does_item_exist(hint_tag):
            dpg.configure_item(hint_tag, show=True)
        if dpg.does_item_exist(mix_tag):
            dpg.configure_item(mix_tag, show=False)
        if dpg.does_item_exist(rnd_tag):
            dpg.configure_item(rnd_tag, show=False)
        if dpg.does_item_exist(dith_tag):
            dpg.configure_item(dith_tag, show=False)
        return

    state = _FX_STATE.get(kind, "off")
    label_ru = EFFECT_LABELS_RU.get(kind, kind)
    state_color = {
        "off":    _GL_DIM,
        "on":     _GL_GREEN,
        "random": _GL_MAGENTA,
        "locked": _GL_CYAN,
    }.get(state, _GL_TEXT)

    if dpg.does_item_exist(name_tag):
        dpg.configure_item(name_tag,
                           default_value=f"{label_ru}  [{_STATE_LABELS[state]}]",
                           color=state_color)
    if dpg.does_item_exist(hint_tag):
        dpg.configure_item(hint_tag, show=False)

    no_mix = kind in USER_EFFECTS_NO_MIX
    show_mix = not no_mix
    if dpg.does_item_exist(mix_tag):
        dpg.configure_item(mix_tag, show=show_mix)
        # mirror the per-effect hidden slider to the side panel slider
        live = f"fx_mix_{kind}"
        if show_mix and dpg.does_item_exist(live):
            dpg.set_value(mix_tag, float(dpg.get_value(live)))
            dpg.configure_item(mix_tag, enabled=(state in ("on", "random", "locked")))
    if dpg.does_item_exist(rnd_tag):
        dpg.configure_item(rnd_tag, show=show_mix)
        live = f"fx_randmix_{kind}"
        if show_mix and dpg.does_item_exist(live):
            dpg.set_value(rnd_tag, bool(dpg.get_value(live)))
            dpg.configure_item(rnd_tag, enabled=(state in ("on", "random", "locked")))

    # compositing-mode combo: only for effects whose shader honours u_blend
    if dpg.does_item_exist("fx_side_blend"):
        show_blend = kind in _BLEND_FX
        dpg.configure_item("fx_side_blend", show=show_blend)
        if show_blend:
            idx = int(_FX_BLEND.get(kind, 0))
            idx = max(0, min(len(_BLEND_MODES) - 1, idx))
            dpg.set_value("fx_side_blend", _BLEND_MODES[idx])

    # dither sub-panel only when dither is selected
    if dpg.does_item_exist(dith_tag):
        dpg.configure_item(dith_tag, show=(kind == "dither"))
    # ASCII sub-panel only when ascii_mosaic is selected
    if dpg.does_item_exist("fx_side_ascii"):
        dpg.configure_item("fx_side_ascii", show=(kind == "ascii_mosaic"))
    # Pixel sort sub-panel only when pixel_sort is selected
    if dpg.does_item_exist("fx_side_psort"):
        dpg.configure_item("fx_side_psort", show=(kind == "pixel_sort"))
    # Kaleidoscope sub-panel only when kaleidoscope is selected
    if dpg.does_item_exist("fx_side_kaleido"):
        dpg.configure_item("fx_side_kaleido", show=(kind == "kaleidoscope"))
    # Slit-scan sub-panel only when slit_scan is selected
    if dpg.does_item_exist("fx_side_slit"):
        dpg.configure_item("fx_side_slit", show=(kind == "slit_scan"))
    # Distort sub-panel only when distort is selected
    if dpg.does_item_exist("fx_side_distort"):
        dpg.configure_item("fx_side_distort", show=(kind == "distort"))
    # All remaining sub-panels — show the matching one, hide the rest
    for _k in (
        "thermal", "cellular", "elastic_grid", "crt_monitor",
        "glitch", "modulate", "pixelate",
        "vhs", "crt", "wave_warp", "datamosh", "motion_trails",
        "rgb_split", "duotone", "luma_key", "edges", "pixel_swap",
        "found_footage", "chromatic_aberration", "color_posterize",
        "swirl", "iridescent_foil", "motion_blur", "advanced_glitch",
    ):
        _tag = f"fx_side_{_k}"
        if dpg.does_item_exist(_tag):
            dpg.configure_item(_tag, show=(kind == _k))


def _on_side_mix_change(_s, value):
    """Side-panel mix slider → mirror back to the per-effect hidden slider."""
    if not _SELECTED_FX:
        return
    live = f"fx_mix_{_SELECTED_FX}"
    if dpg.does_item_exist(live):
        dpg.set_value(live, float(value))


def _on_side_rnd_change(_s, value):
    if not _SELECTED_FX:
        return
    live = f"fx_randmix_{_SELECTED_FX}"
    if dpg.does_item_exist(live):
        dpg.set_value(live, bool(value))


def _on_side_blend_change(_s, value):
    """Side-panel blend combo → store per-effect compositing mode index."""
    if not _SELECTED_FX:
        return
    try:
        _FX_BLEND[_SELECTED_FX] = _BLEND_MODES.index(value)
    except ValueError:
        _FX_BLEND[_SELECTED_FX] = 0


# ═══════════════════════════════════════════════════════════════════════════════
# palette
# ═══════════════════════════════════════════════════════════════════════════════

def _color_rgb01(tag: str) -> tuple:
    v = dpg.get_value(tag)
    return (v[0] / 255.0, v[1] / 255.0, v[2] / 255.0)


# Plain float sliders that map 1:1 to state.Params field names.
_AM_DITHER_SCALARS = (
    "dither_levels", "dither_grid_size",
    "dither_osc_a_freq", "dither_osc_b_freq",
    "dither_osc_a_speed", "dither_osc_b_speed",
    "dither_trace_thickness",
    "dither_jitter", "dither_chromatic",
)


def _apply_ascii_preset(_s, _a, text: str):
    if dpg.does_item_exist("am_ascii_text"):
        dpg.set_value("am_ascii_text", text)


def _apply_blob_track(d: dict) -> None:
    """Push every saved blob_track field back into its widget."""
    if not d:
        return
    def _set(tag, val):
        if dpg.does_item_exist(tag):
            try: dpg.set_value(tag, val)
            except Exception: pass
    _set("bt_enabled",   bool(d.get("enabled",  False)))
    _set("bt_zoom",      bool(d.get("zoom_to_blob", False)))
    _set("bt_hue",       int(d.get("hue_center", 120)))
    _set("bt_hue_range", int(d.get("hue_range",   30)))
    _set("bt_min_val",   int(d.get("min_val",     80)))
    _set("bt_min_sat",   int(d.get("min_sat",     50)))
    _set("bt_min_size",  int(d.get("min_size",   800)))
    _set("bt_max_blobs", int(d.get("max_blobs",    3)))
    _set("bt_overlay",   bool(d.get("show_overlay",  True)))
    _set("bt_labels",    bool(d.get("show_labels",   True)))
    _set("bt_centroid",  bool(d.get("show_centroid", True)))
    _set("bt_dataline",  bool(d.get("show_data_line",True)))
    _set("bt_thickness", int(d.get("bracket_thickness", 2)))
    bc = d.get("bracket_color")
    if bc and len(bc) >= 3:
        _set("bt_col", [int(bc[0]), int(bc[1]), int(bc[2]), 255])
    # tracker mode (key → display label)
    mode = str(d.get("tracker_mode", "hsv"))
    try:
        idx = _BLOB_TRACKER_KEYS.index(mode)
    except ValueError:
        idx = 0
    _set("bt_tracker_mode_combo", _BLOB_TRACKER_MODES[idx])
    # ── visual extras ──────────────────────────────────────────────────
    _set("bt_crosshair",  bool(d.get("show_crosshair", False)))
    _set("bt_leader",     bool(d.get("show_leader",    False)))
    _set("bt_trails",     bool(d.get("show_trails",    False)))
    _set("bt_trail_len",  int(d.get("trail_length",       12)))
    _set("bt_network",    bool(d.get("show_network",   False)))
    _set("bt_label_prefix", str(d.get("label_prefix", "TGT")))
    _set("bt_extra_data", bool(d.get("show_extra_data", False)))
    _set("bt_timestamp",  bool(d.get("show_timestamp", False)))
    bs = int(d.get("bracket_style", 0))
    bs = max(0, min(len(_BLOB_BRACKET_STYLES) - 1, bs))
    _set("bt_bracket_style_combo", _BLOB_BRACKET_STYLES[bs])
    tf = int(d.get("timestamp_format", 0))
    tf = max(0, min(len(_BLOB_TS_FORMATS) - 1, tf))
    _set("bt_ts_format_combo", _BLOB_TS_FORMATS[tf])


def _collect_psort_params() -> dict:
    """Pixel sort overrides — algo combo + threshold + length + feature mode."""
    out: dict = {}
    if dpg.does_item_exist("am_psort_algo_combo"):
        try:
            idx = _PSORT_ALGO_LABELS.index(dpg.get_value("am_psort_algo_combo"))
        except ValueError:
            idx = 0
        out["psort_algorithm"] = float(idx)
    if dpg.does_item_exist("am_psort_threshold"):
        out["psort_threshold"] = float(dpg.get_value("am_psort_threshold"))
    if dpg.does_item_exist("am_psort_length"):
        out["psort_length"] = float(dpg.get_value("am_psort_length"))
    if dpg.does_item_exist("am_psort_color_mode_combo"):
        try:
            idx = _PSORT_COLOR_MODES.index(
                dpg.get_value("am_psort_color_mode_combo"))
        except ValueError:
            idx = 0
        out["psort_color_mode"] = float(idx)
    return out


def _apply_psort_params(d: dict) -> None:
    if not d:
        return
    algo = d.get("psort_algorithm")
    if algo is not None and dpg.does_item_exist("am_psort_algo_combo"):
        idx = max(0, min(len(_PSORT_ALGO_LABELS) - 1, int(round(float(algo)))))
        dpg.set_value("am_psort_algo_combo", _PSORT_ALGO_LABELS[idx])
    if "psort_threshold" in d and dpg.does_item_exist("am_psort_threshold"):
        dpg.set_value("am_psort_threshold", float(d["psort_threshold"]))
    if "psort_length" in d and dpg.does_item_exist("am_psort_length"):
        dpg.set_value("am_psort_length", float(d["psort_length"]))
    cm = d.get("psort_color_mode")
    if cm is not None and dpg.does_item_exist("am_psort_color_mode_combo"):
        idx = max(0, min(len(_PSORT_COLOR_MODES) - 1, int(round(float(cm)))))
        dpg.set_value("am_psort_color_mode_combo", _PSORT_COLOR_MODES[idx])


# Slit-scan scroll direction (label → slit_direction value).
_SLIT_DIR_LABELS = ("0 · Вертикаль (строки)", "1 · Горизонталь (колонки)")


def _collect_kaleido_params() -> dict:
    """Kaleidoscope overrides — sides / angle / spin / zoom."""
    out: dict = {}
    for key, tag in (("kaleido_sides", "am_kaleido_sides"),
                     ("kaleido_angle", "am_kaleido_angle"),
                     ("kaleido_spin",  "am_kaleido_spin"),
                     ("kaleido_zoom",  "am_kaleido_zoom")):
        if dpg.does_item_exist(tag):
            out[key] = float(dpg.get_value(tag))
    return out


def _apply_kaleido_params(d: dict) -> None:
    if not d:
        return
    for key, tag in (("kaleido_sides", "am_kaleido_sides"),
                     ("kaleido_angle", "am_kaleido_angle"),
                     ("kaleido_spin",  "am_kaleido_spin"),
                     ("kaleido_zoom",  "am_kaleido_zoom")):
        if key in d and dpg.does_item_exist(tag):
            dpg.set_value(tag, float(d[key]))


def _collect_slit_params() -> dict:
    """Slit-scan overrides — direction combo + speed / scanpos / feedback."""
    out: dict = {}
    if dpg.does_item_exist("am_slit_dir_combo"):
        try:
            idx = _SLIT_DIR_LABELS.index(dpg.get_value("am_slit_dir_combo"))
        except ValueError:
            idx = 0
        out["slit_direction"] = float(idx)
    for key, tag in (("slit_speed",     "am_slit_speed"),
                     ("slit_scanpos",   "am_slit_scanpos"),
                     ("slit_feedback",  "am_slit_feedback"),
                     ("slit_threshold", "am_slit_threshold")):
        if dpg.does_item_exist(tag):
            out[key] = float(dpg.get_value(tag))
    return out


def _apply_slit_params(d: dict) -> None:
    if not d:
        return
    sd = d.get("slit_direction")
    if sd is not None and dpg.does_item_exist("am_slit_dir_combo"):
        idx = max(0, min(len(_SLIT_DIR_LABELS) - 1, int(round(float(sd)))))
        dpg.set_value("am_slit_dir_combo", _SLIT_DIR_LABELS[idx])
    for key, tag in (("slit_speed",     "am_slit_speed"),
                     ("slit_scanpos",   "am_slit_scanpos"),
                     ("slit_feedback",  "am_slit_feedback"),
                     ("slit_threshold", "am_slit_threshold")):
        if key in d and dpg.does_item_exist(tag):
            dpg.set_value(tag, float(d[key]))


_DISTORT_KEYS = (
    ("distort_xshift",    "am_distort_xshift"),
    ("distort_yshift",    "am_distort_yshift"),
    ("distort_threshold", "am_distort_threshold"),
    ("distort_scale",     "am_distort_scale"),
    ("distort_speed",     "am_distort_speed"),
    ("distort_blur",      "am_distort_blur"),
    ("distort_grain",     "am_distort_grain"),
    ("distort_gamma",     "am_distort_gamma"),
    ("distort_black",     "am_distort_black"),
    ("distort_white",     "am_distort_white"),
)


_DISTORT_NOISE_MODES = ("Value-noise (fBM)", "Simplex (LYGIA)")


def _collect_distort_params() -> dict:
    """Distort overrides — procedural displacement-map controls."""
    out: dict = {}
    for key, tag in _DISTORT_KEYS:
        if dpg.does_item_exist(tag):
            out[key] = float(dpg.get_value(tag))
    if dpg.does_item_exist("am_distort_noise_mode"):
        out["distort_noise_mode"] = _combo_index("am_distort_noise_mode",
                                                  _DISTORT_NOISE_MODES)
    return out


def _apply_distort_params(d: dict) -> None:
    if not d:
        return
    for key, tag in _DISTORT_KEYS:
        if key in d and dpg.does_item_exist(tag):
            dpg.set_value(tag, float(d[key]))
    if "distort_noise_mode" in d:
        _set_combo_index("am_distort_noise_mode", _DISTORT_NOISE_MODES,
                         d["distort_noise_mode"])


# ─────────────────────────────────────────────────────────────────────────────
#  New-effect overrides (thermal / cellular / elastic_grid / crt_monitor /
#  glitch / modulate).  Each follows the same field-name → widget-tag pattern as
#  distort.  Discrete params use a combo (stored as the option index); cellular
#  uses a colour picker for the living-cell tint.
# ─────────────────────────────────────────────────────────────────────────────

_THERMAL_PALETTES = ("Infrared", "Ironbow", "Rainbow", "Arctic", "Heatmap (LYGIA)")
_CA_MODES         = ("Life (B3/S23)", "Decay-diffusion")
_MOD_MODES        = ("AM (амплитуда)", "FM (частота)", "Phase (фаза)", "RGB-полосы (радуга)")
_EG_NOISE_MODES   = ("Hash (sin/cos)", "Simplex (LYGIA)")


def _combo_index(tag: str, options: tuple) -> float:
    """Return the selected combo option's index as a float (0.0 fallback)."""
    if not dpg.does_item_exist(tag):
        return 0.0
    val = dpg.get_value(tag)
    try:
        return float(options.index(val))
    except (ValueError, TypeError):
        return 0.0


def _set_combo_index(tag: str, options: tuple, idx) -> None:
    if dpg.does_item_exist(tag):
        i = max(0, min(len(options) - 1, int(round(float(idx)))))
        dpg.set_value(tag, options[i])


# (param field, widget tag) — plain float sliders only.
_THERMAL_SLIDERS = (
    ("thermal_gain",   "am_thermal_gain"),
    ("thermal_gamma",  "am_thermal_gamma"),
    ("thermal_levels", "am_thermal_levels"),
)
_ELASTIC_SLIDERS = (
    ("eg_grid",     "am_eg_grid"),
    ("eg_strength", "am_eg_strength"),
    ("eg_speed",    "am_eg_speed"),
    ("eg_jitter",   "am_eg_jitter"),
)
_CRTM_SLIDERS = (
    ("crtm_curvature",  "am_crtm_curvature"),
    ("crtm_scanline",   "am_crtm_scanline"),
    ("crtm_grille",     "am_crtm_grille"),
    ("crtm_vignette",   "am_crtm_vignette"),
    ("crtm_bloom",      "am_crtm_bloom"),
    ("crtm_aberration", "am_crtm_aberration"),
)
_GLITCH_SLIDERS = (
    ("glitch_block_size",    "am_glitch_block_size"),
    ("glitch_drop_chance",   "am_glitch_drop_chance"),
    ("glitch_repeat_chance", "am_glitch_repeat_chance"),
)
_CA_SLIDERS = (
    ("ca_cell",     "am_ca_cell"),
    ("ca_rate",     "am_ca_rate"),
    ("ca_seed_thr", "am_ca_seed_thr"),
)
_MOD_SLIDERS = (
    ("mod_freq",   "am_mod_freq"),
    ("mod_amount", "am_mod_amount"),
    ("mod_speed",  "am_mod_speed"),
    ("mod_axis",   "am_mod_axis"),
)
_PIXELATE_SLIDERS = (
    ("pixelate_block",    "am_pixelate_block"),
    ("pixelate_feedback", "am_pixelate_feedback"),
    ("pixelate_zoom",     "am_pixelate_zoom"),
    ("pixelate_drift",    "am_pixelate_drift"),
)
_PIXELATE_FB_MODES = ("Lighten (свечение)", "Add (сложение)", "Over (наложение)")


def _collect_sliders(pairs) -> dict:
    out: dict = {}
    for key, tag in pairs:
        if dpg.does_item_exist(tag):
            out[key] = float(dpg.get_value(tag))
    return out


def _apply_sliders(pairs, d: dict) -> None:
    for key, tag in pairs:
        if key in d and dpg.does_item_exist(tag):
            dpg.set_value(tag, float(d[key]))


def _collect_thermal_params() -> dict:
    out = _collect_sliders(_THERMAL_SLIDERS)
    if dpg.does_item_exist("am_thermal_palette"):
        out["thermal_palette"] = _combo_index("am_thermal_palette", _THERMAL_PALETTES)
    return out


def _apply_thermal_params(d: dict) -> None:
    if not d:
        return
    _apply_sliders(_THERMAL_SLIDERS, d)
    if "thermal_palette" in d:
        _set_combo_index("am_thermal_palette", _THERMAL_PALETTES, d["thermal_palette"])


def _collect_cellular_params() -> dict:
    out = _collect_sliders(_CA_SLIDERS)
    if dpg.does_item_exist("am_ca_mode"):
        out["ca_mode"] = _combo_index("am_ca_mode", _CA_MODES)
    if dpg.does_item_exist("am_ca_tint"):
        r, g, b = _color_rgb01("am_ca_tint")
        out["ca_tint_r"], out["ca_tint_g"], out["ca_tint_b"] = r, g, b
    return out


def _apply_cellular_params(d: dict) -> None:
    if not d:
        return
    _apply_sliders(_CA_SLIDERS, d)
    if "ca_mode" in d:
        _set_combo_index("am_ca_mode", _CA_MODES, d["ca_mode"])
    if all(k in d for k in ("ca_tint_r", "ca_tint_g", "ca_tint_b")) \
            and dpg.does_item_exist("am_ca_tint"):
        dpg.set_value("am_ca_tint",
                      (int(float(d["ca_tint_r"]) * 255),
                       int(float(d["ca_tint_g"]) * 255),
                       int(float(d["ca_tint_b"]) * 255), 255))


def _collect_elastic_grid_params() -> dict:
    out = _collect_sliders(_ELASTIC_SLIDERS)
    if dpg.does_item_exist("am_eg_noise_mode"):
        out["eg_noise_mode"] = _combo_index("am_eg_noise_mode", _EG_NOISE_MODES)
    return out


def _apply_elastic_grid_params(d: dict) -> None:
    if not d:
        return
    _apply_sliders(_ELASTIC_SLIDERS, d)
    if "eg_noise_mode" in d:
        _set_combo_index("am_eg_noise_mode", _EG_NOISE_MODES, d["eg_noise_mode"])


def _collect_crt_monitor_params() -> dict:
    return _collect_sliders(_CRTM_SLIDERS)


def _apply_crt_monitor_params(d: dict) -> None:
    if d:
        _apply_sliders(_CRTM_SLIDERS, d)


def _collect_glitch_params() -> dict:
    return _collect_sliders(_GLITCH_SLIDERS)


def _apply_glitch_params(d: dict) -> None:
    if d:
        _apply_sliders(_GLITCH_SLIDERS, d)


def _collect_modulate_params() -> dict:
    out = _collect_sliders(_MOD_SLIDERS)
    if dpg.does_item_exist("am_mod_mode"):
        out["mod_mode"] = _combo_index("am_mod_mode", _MOD_MODES)
    return out


def _apply_modulate_params(d: dict) -> None:
    if not d:
        return
    _apply_sliders(_MOD_SLIDERS, d)
    if "mod_mode" in d:
        _set_combo_index("am_mod_mode", _MOD_MODES, d["mod_mode"])


def _collect_pixelate_params() -> dict:
    out = _collect_sliders(_PIXELATE_SLIDERS)
    if dpg.does_item_exist("am_pixelate_fb_mode"):
        out["pixelate_fb_mode"] = _combo_index("am_pixelate_fb_mode",
                                               _PIXELATE_FB_MODES)
    return out


def _apply_pixelate_params(d: dict) -> None:
    if not d:
        return
    _apply_sliders(_PIXELATE_SLIDERS, d)
    if "pixelate_fb_mode" in d:
        _set_combo_index("am_pixelate_fb_mode", _PIXELATE_FB_MODES,
                         d["pixelate_fb_mode"])


def _collect_ascii_text() -> str:
    """User-typed glyph string for the ASCII atlas (empty = use default)."""
    if dpg.does_item_exist("am_ascii_text"):
        return str(dpg.get_value("am_ascii_text") or "")
    return ""


def _collect_ascii_params() -> dict:
    """Per-cut numeric ASCII overrides (cell_size, invert, color_source, tint).
    Mirror of `_collect_dither_params`."""
    out: dict = {}
    if dpg.does_item_exist("am_ascii_cell_size"):
        out["ascii_cell_size"] = float(dpg.get_value("am_ascii_cell_size"))
    if dpg.does_item_exist("am_ascii_invert_chk"):
        out["ascii_invert"] = 1.0 if dpg.get_value("am_ascii_invert_chk") else 0.0
    if dpg.does_item_exist("am_ascii_color_source_chk"):
        out["ascii_color_source"] = 1.0 if dpg.get_value("am_ascii_color_source_chk") else 0.0
    if dpg.does_item_exist("am_ascii_tint"):
        r, g, b = _color_rgb01("am_ascii_tint")
        out["ascii_tint_r"] = r
        out["ascii_tint_g"] = g
        out["ascii_tint_b"] = b
    return out


def _apply_ascii_dict(text: str, params: dict) -> None:
    """Reverse of _collect_ascii_text + _collect_ascii_params."""
    if text and dpg.does_item_exist("am_ascii_text"):
        dpg.set_value("am_ascii_text", text)
    if not params:
        return
    if "ascii_cell_size" in params and dpg.does_item_exist("am_ascii_cell_size"):
        dpg.set_value("am_ascii_cell_size", float(params["ascii_cell_size"]))
    if "ascii_invert" in params and dpg.does_item_exist("am_ascii_invert_chk"):
        dpg.set_value("am_ascii_invert_chk", float(params["ascii_invert"]) > 0.5)
    if "ascii_color_source" in params and dpg.does_item_exist("am_ascii_color_source_chk"):
        dpg.set_value("am_ascii_color_source_chk", float(params["ascii_color_source"]) > 0.5)
    if all(k in params for k in ("ascii_tint_r", "ascii_tint_g", "ascii_tint_b")) \
            and dpg.does_item_exist("am_ascii_tint"):
        dpg.set_value("am_ascii_tint",
                      (int(float(params["ascii_tint_r"]) * 255),
                       int(float(params["ascii_tint_g"]) * 255),
                       int(float(params["ascii_tint_b"]) * 255), 255))


def _collect_dither_params() -> dict:
    """Read every Dither widget and return a dict of state.Params field
    names → float values. Empty dict if the panel hasn't been built
    (defensive — won't crash if the section was never opened/built).
    """
    out: dict = {}
    if not dpg.does_item_exist("am_dither_levels"):
        return out
    # ── plain float sliders ──
    for k in _AM_DITHER_SCALARS:
        tag = "am_" + k
        if dpg.does_item_exist(tag):
            out[k] = float(dpg.get_value(tag))
    # ── combo: algorithm (circuit / bayer4 / bayer8 / blue noise → 0..3) ──
    if dpg.does_item_exist("am_dither_algo_combo"):
        try:
            idx = _DITHER_ALGOS.index(dpg.get_value("am_dither_algo_combo"))
        except ValueError:
            idx = 0
        out["dither_algorithm"] = float(idx)
    # ── combo: mix_mode (avg/mul/xor/cross → 0..3) ──
    if dpg.does_item_exist("am_dither_mix_mode_combo"):
        try:
            idx = _DITHER_MIX_MODES.index(
                dpg.get_value("am_dither_mix_mode_combo"))
        except ValueError:
            idx = 0
        out["dither_mix_mode"] = float(idx)
    # ── combo: color_mode (mono/channels/duotone → 0..2) ──
    if dpg.does_item_exist("am_dither_color_mode_combo"):
        try:
            idx = _DITHER_COLOR_MODES.index(
                dpg.get_value("am_dither_color_mode_combo"))
        except ValueError:
            idx = 0
        out["dither_color_mode"] = float(idx)
    # ── checkbox: polarity (0/1) ──
    if dpg.does_item_exist("am_dither_polarity_chk"):
        out["dither_polarity"] = 1.0 if dpg.get_value("am_dither_polarity_chk") else 0.0
    # ── color A / color B ──
    if dpg.does_item_exist("am_dither_color_a"):
        r, g, b = _color_rgb01("am_dither_color_a")
        out["dither_color_a_r"] = r
        out["dither_color_a_g"] = g
        out["dither_color_a_b"] = b
    if dpg.does_item_exist("am_dither_color_b"):
        r, g, b = _color_rgb01("am_dither_color_b")
        out["dither_color_b_r"] = r
        out["dither_color_b_g"] = g
        out["dither_color_b_b"] = b
    return out


def _collect_palette():
    name = dpg.get_value("palette_combo")
    if name != "custom":
        return name
    return {
        "shadow":    _color_rgb01("pal_shadow"),
        "mid":       _color_rgb01("pal_mid"),
        "highlight": _color_rgb01("pal_high"),
    }


def _on_palette_change(_s, val):
    show = (val == "custom")
    for t in ("pal_shadow", "pal_mid", "pal_high"):
        if dpg.does_item_exist(t):
            dpg.configure_item(t, show=show)


# ═══════════════════════════════════════════════════════════════════════════════
# seed
# ═══════════════════════════════════════════════════════════════════════════════

def _ensure_seed() -> int:
    v = int(dpg.get_value("seed_input"))
    if v == 0:
        v = random.randint(1, 2_000_000_000)
        dpg.set_value("seed_input", v)
    return v


def _new_seed_cb(_s=None, _a=None):
    v = random.randint(1, 2_000_000_000)
    dpg.set_value("seed_input", v)


# ═══════════════════════════════════════════════════════════════════════════════
# settings collector
# ═══════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# preset save/load + last-session autosave
# ─────────────────────────────────────────────────────────────────────────────

# Path to the auto-saved "last session" file.  Resolved lazily — APP_DIR can
# be overridden by the entry point (shi_o_kiku.py) before main() runs.
def _last_session_path() -> Path:
    base = APP_DIR if APP_DIR is not None else Path(__file__).parent
    return base / "automatch_last_session.json"


def _apply_dither_params(d: dict) -> None:
    """Push a previously-saved dither_params dict back into the widgets."""
    if not d or not dpg.does_item_exist("am_dither_levels"):
        return
    for k in _AM_DITHER_SCALARS:
        if k in d and dpg.does_item_exist("am_" + k):
            dpg.set_value("am_" + k, float(d[k]))
    # combos
    al = d.get("dither_algorithm")
    if al is not None and dpg.does_item_exist("am_dither_algo_combo"):
        idx = max(0, min(len(_DITHER_ALGOS) - 1, int(round(float(al)))))
        dpg.set_value("am_dither_algo_combo", _DITHER_ALGOS[idx])
    mm = d.get("dither_mix_mode")
    if mm is not None and dpg.does_item_exist("am_dither_mix_mode_combo"):
        idx = max(0, min(len(_DITHER_MIX_MODES) - 1, int(round(float(mm)))))
        dpg.set_value("am_dither_mix_mode_combo", _DITHER_MIX_MODES[idx])
    cm = d.get("dither_color_mode")
    if cm is not None and dpg.does_item_exist("am_dither_color_mode_combo"):
        idx = max(0, min(len(_DITHER_COLOR_MODES) - 1, int(round(float(cm)))))
        dpg.set_value("am_dither_color_mode_combo", _DITHER_COLOR_MODES[idx])
    # checkbox
    if "dither_polarity" in d and dpg.does_item_exist("am_dither_polarity_chk"):
        dpg.set_value("am_dither_polarity_chk", float(d["dither_polarity"]) > 0.5)
    # colours
    def _set_rgb(tag, r, g, b):
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, (int(r * 255), int(g * 255), int(b * 255), 255))
    if all(k in d for k in ("dither_color_a_r", "dither_color_a_g", "dither_color_a_b")):
        _set_rgb("am_dither_color_a",
                 d["dither_color_a_r"], d["dither_color_a_g"], d["dither_color_a_b"])
    if all(k in d for k in ("dither_color_b_r", "dither_color_b_g", "dither_color_b_b")):
        _set_rgb("am_dither_color_b",
                 d["dither_color_b_r"], d["dither_color_b_g"], d["dither_color_b_b"])


def _dump_session() -> dict:
    """Snapshot every GUI control into a json-serialisable dict."""
    s = _collect_settings()
    return {
        "version": 1,
        "paths": {
            "audio":  STATE.get("audio", ""),
            "media":  STATE.get("media", ""),
            "output": STATE.get("output", ""),
        },
        "settings": {
            "duration":            s.duration,
            "fps":                 s.fps,
            "output_w":            s.output_w,
            "output_h":            s.output_h,
            "cuts_per_beat":       s.cuts_per_beat,
            "aspect_mode":         s.aspect_mode,
            "beat_flash_ms":       s.beat_flash_ms,
            "audio_offset_sec":    s.audio_offset_sec,
            "override_bpm":        s.override_bpm,
            "crf":                 s.crf,
            "palette":             s.palette,             # str or dict
            "palette_strength":    s.palette_strength,
            "crossfade_ms":        s.crossfade_ms,
            "transition_mode":     s.transition_mode,
            "seed":                s.seed,
            "clip_mode":           s.clip_mode,
            "clip_stride":         s.clip_stride,
            "clip_avoid_recent":   s.clip_avoid_recent,
            "beat_remap_strength": s.beat_remap_strength,
            "cam_stabilize":       s.cam_stabilize,
            "random_clip_starts":  s.random_clip_starts,
            "single_video_clips":  s.single_video_clips,
            "preview_scale":       s.preview_scale,
            "dither_params":       s.dither_params,
            "ascii_text":          s.ascii_text,
            "ascii_params":        s.ascii_params,
            "psort_params":        s.psort_params,
            "kaleido_params":      s.kaleido_params,
            "slit_params":         s.slit_params,
            "distort_params":      s.distort_params,
            "thermal_params":      s.thermal_params,
            "cellular_params":     s.cellular_params,
            "elastic_grid_params": s.elastic_grid_params,
            "crt_monitor_params":  s.crt_monitor_params,
            "glitch_params":       s.glitch_params,
            "modulate_params":     s.modulate_params,
            "pixelate_params":     s.pixelate_params,
            "effect_overrides":    s.effect_overrides,
            "text_cards":          asdict(s.text_cards),
            "blob_track":          asdict(s.blob_track),
            "mod_config":          s.mod_config,
            "effect_chain":        s.effect_chain,
            "chain_enabled":       bool(dpg.get_value("chain_enabled")) if dpg.does_item_exist("chain_enabled") else False,
        },
        "fx_state": dict(_FX_STATE),
    }


def _apply_session(data: dict) -> None:
    """Apply a previously-dumped session dict back onto the widgets."""
    # ── paths ──
    paths = data.get("paths", {})
    if paths.get("audio"):
        STATE["audio"] = paths["audio"]
        if dpg.does_item_exist("audio_path"):
            dpg.set_value("audio_path", paths["audio"])
    if paths.get("media"):
        STATE["media"] = paths["media"]
        _refresh_media_label()
    if paths.get("output"):
        STATE["output"] = paths["output"]
        if dpg.does_item_exist("output_path"):
            dpg.set_value("output_path", paths["output"])

    # ── numeric/bool settings → widget tags ──
    s = data.get("settings", {})
    SIMPLE = [
        ("duration",        "duration"),
        ("fps",             "fps"),
        ("width",           "output_w"),
        ("height",          "output_h"),
        ("flash_ms",        "beat_flash_ms"),
        ("offset",          "audio_offset_sec"),
        ("override_bpm",    "override_bpm"),
        ("crf",             "crf"),
        ("palette_strength","palette_strength"),
        ("crossfade_ms",    "crossfade_ms"),
        ("seed_input",      "seed"),
    ]
    for tag, key in SIMPLE:
        if key in s and dpg.does_item_exist(tag):
            try:
                dpg.set_value(tag, s[key])
            except Exception:
                pass

    # ── discrete combos: snap stored float → nearest label ──
    if "cuts_per_beat" in s and dpg.does_item_exist("cuts_per_beat_combo"):
        dpg.set_value("cuts_per_beat_combo",
                      _snap_to_label(s["cuts_per_beat"], _CUTS_PER_BEAT_LABELS))
    if "transition_mode" in s and dpg.does_item_exist("transition_mode_combo"):
        dpg.set_value("transition_mode_combo",
                      _TRANSITION_MODE_LABELS_REV.get(s["transition_mode"],
                                                      "Random (микс)"))
    if "beat_remap_strength" in s and dpg.does_item_exist("beat_remap_combo"):
        dpg.set_value("beat_remap_combo",
                      _snap_to_label(s["beat_remap_strength"], _BEAT_REMAP_LABELS))

    # ── aspect mode (defaults to "horizontal" if absent) ──
    mode = s.get("aspect_mode", "horizontal")
    if mode in _ASPECT_LABELS and dpg.does_item_exist("aspect_mode_combo"):
        dpg.set_value("aspect_mode_combo", _ASPECT_LABELS[mode])
        _on_aspect_mode_change()
    for tag, key in [("cam_stabilize",      "cam_stabilize"),
                     ("random_clip_starts", "random_clip_starts")]:
        if key in s and dpg.does_item_exist(tag):
            dpg.set_value(tag, bool(s[key]))
    if "single_video_clips" in s and dpg.does_item_exist("single_video_clips"):
        dpg.set_value("single_video_clips", int(s["single_video_clips"]))
    # clip_mode: accept old legacy bool ("clip_random") and new combo string
    if "clip_mode" in s and dpg.does_item_exist("clip_mode_combo"):
        m = s["clip_mode"]
        # backward compat: ancient sessions stored True/False
        if isinstance(m, bool):
            m = "random" if m else "bag"
        if m in ("bag", "shuffle", "random", "stride", "reverse"):
            dpg.set_value("clip_mode_combo", m)
    if "clip_stride" in s and dpg.does_item_exist("clip_stride"):
        dpg.set_value("clip_stride", int(s["clip_stride"]))
    if "clip_avoid_recent" in s and dpg.does_item_exist("clip_avoid_recent"):
        dpg.set_value("clip_avoid_recent", int(s["clip_avoid_recent"]))
    if "preview_scale" in s and dpg.does_item_exist("preview_scale"):
        dpg.set_value("preview_scale", float(s["preview_scale"]))

    # ── palette: str or {shadow,mid,highlight} dict ──
    pal = s.get("palette")
    if isinstance(pal, str) and dpg.does_item_exist("palette_combo"):
        items = list(PALETTES.keys()) + ["custom"]
        if pal in items:
            dpg.set_value("palette_combo", pal)
            try:
                _on_palette_change(None, pal)
            except Exception:
                pass
    elif isinstance(pal, dict):
        if dpg.does_item_exist("palette_combo"):
            dpg.set_value("palette_combo", "custom")
            try:
                _on_palette_change(None, "custom")
            except Exception:
                pass
        for slot, tag in [("shadow", "pal_shadow"),
                          ("mid",    "pal_mid"),
                          ("highlight", "pal_high")]:
            if slot in pal and dpg.does_item_exist(tag):
                r, g, b = [int(float(v) * 255) for v in pal[slot][:3]]
                dpg.set_value(tag, (r, g, b, 255))

    # ── dither detailed panel ──
    _apply_dither_params(s.get("dither_params", {}))

    # ── ASCII glyphs + numeric overrides ──
    _apply_ascii_dict(s.get("ascii_text", "") or "",
                      s.get("ascii_params", {}) or {})

    # ── Pixel sort overrides ──
    _apply_psort_params(s.get("psort_params", {}) or {})

    # ── Kaleidoscope + slit-scan overrides ──
    _apply_kaleido_params(s.get("kaleido_params", {}) or {})
    _apply_slit_params(s.get("slit_params", {}) or {})
    _apply_distort_params(s.get("distort_params", {}) or {})

    # ── New effects overrides ──
    _apply_thermal_params(s.get("thermal_params", {}) or {})
    _apply_cellular_params(s.get("cellular_params", {}) or {})
    _apply_elastic_grid_params(s.get("elastic_grid_params", {}) or {})
    _apply_crt_monitor_params(s.get("crt_monitor_params", {}) or {})
    _apply_glitch_params(s.get("glitch_params", {}) or {})
    _apply_modulate_params(s.get("modulate_params", {}) or {})
    _apply_pixelate_params(s.get("pixelate_params", {}) or {})

    # ── Blob track (full restore: incl. tracker_mode) ──
    _apply_blob_track(s.get("blob_track", {}) or {})

    # ── Mod Matrix ──
    _apply_mod_config(s.get("mod_config", {}) or {})

    # ── Effect Chain ──
    saved_chain = s.get("effect_chain") or []
    _apply_effect_chain_gui(saved_chain)
    if s.get("chain_enabled") and dpg.does_item_exist("chain_enabled"):
        dpg.set_value("chain_enabled", True)

    # ── text cards: full restore (enabled / lines / vibe / colours / …) ──
    _tc = s.get("text_cards", {}) or {}
    _apply_text_cards(_tc)
    # font picker selection (label, not raw path) + live preview refresh
    if dpg.does_item_exist("tc_font"):
        dpg.set_value("tc_font", _font_path_to_label(_tc.get("font_path", "")))
        _refresh_tc_preview()

    # ── effects matrix state (off/on/RND/locked; legacy "auto" → "off") ──
    for kind, st in (data.get("fx_state") or {}).items():
        if kind not in USER_EFFECTS:
            continue
        if st == "auto":            # legacy sessions
            st = "off"
        if st in _STATE_CYCLE:
            try:
                _set_fx_state(kind, st)
            except Exception:
                pass

    # ── effect mix overrides per kind (if state ON/RND) ──
    overrides = s.get("effect_overrides") or {}
    for kind, ov in overrides.items():
        mix_tag = f"fx_mix_{kind}"
        rnd_tag = f"fx_randmix_{kind}"
        if "mix" in ov and dpg.does_item_exist(mix_tag):
            dpg.set_value(mix_tag, float(ov["mix"]))
        if "random_mix" in ov and dpg.does_item_exist(rnd_tag):
            dpg.set_value(rnd_tag, bool(ov["random_mix"]))
        if "blend" in ov and kind in _FX_BLEND:
            try:
                _FX_BLEND[kind] = int(round(float(ov["blend"])))
            except Exception:
                _FX_BLEND[kind] = 0


def _save_session_json(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_dump_session(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        print(f"[automatch_gui] save_session failed: {e}")
        return False


def _load_session_json(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        _apply_session(data)
        return True
    except Exception:
        print("[automatch_gui] load_session failed:")
        traceback.print_exc()
        return False


def _autosave_last_session() -> None:
    """Best-effort autosave — never raises."""
    try:
        _save_session_json(_last_session_path())
    except Exception:
        pass


def _save_preset_cb(_s=None, _a=None):
    """Open a Save-As dialog (tkinter) and write a preset .json."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        base = APP_DIR if APP_DIR is not None else Path(__file__).parent
        path = filedialog.asksaveasfilename(
            title="Сохранить пресет AutoMatch",
            defaultextension=".json",
            filetypes=[("AutoMatch preset", "*.json"), ("All", "*.*")],
            initialdir=str(base),
            initialfile="automatch_preset.json",
        )
        root.destroy()
    except Exception as e:
        dpg.set_value("status", f"file dialog error: {e}")
        return
    if not path:
        return
    if _save_session_json(Path(path)):
        dpg.set_value("status", f"Пресет сохранён: {Path(path).name}")
    else:
        dpg.set_value("status", "Ошибка при сохранении пресета")


def _load_preset_cb(_s=None, _a=None):
    """Open a Load-File dialog and apply a preset .json."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        base = APP_DIR if APP_DIR is not None else Path(__file__).parent
        path = filedialog.askopenfilename(
            title="Загрузить пресет AutoMatch",
            filetypes=[("AutoMatch preset", "*.json"), ("All", "*.*")],
            initialdir=str(base),
        )
        root.destroy()
    except Exception as e:
        dpg.set_value("status", f"file dialog error: {e}")
        return
    if not path:
        return
    if _load_session_json(Path(path)):
        dpg.set_value("status", f"Пресет загружен: {Path(path).name}")
    else:
        dpg.set_value("status", "Ошибка при загрузке пресета")


# ─── Discrete pickers for cuts/beat, beat remap, aspect mode ───────────────

# Snap cuts/beat to musical subdivisions instead of an arbitrary float slider.
# Label → float fed to AutoEditSettings.cuts_per_beat.
_CUTS_PER_BEAT_LABELS = {
    "1/8":                 0.125,
    "1/4":                 0.25,
    "1/2":                 0.50,
    "×1 (на каждый бит)":  1.00,
    "×2":                  2.00,
    "×4":                  4.00,
}

# Beat-remap intensity steps.
_BEAT_REMAP_LABELS = {
    "off":     0.00,
    "subtle":  0.25,
    "medium":  0.50,
    "strong":  0.75,
    "extreme": 1.00,
}

# Aspect-mode picker labels (Russian → backend mode string).
_ASPECT_MODES_ORDER = ("horizontal", "vertical", "square", "chaos", "custom")
_ASPECT_LABELS = {
    "horizontal": "Горизонтально (16:9, 1280×720)",
    "vertical":   "Вертикально (9:16, 720×1280)",
    "square":     "Квадрат (1:1, 1080×1080)",
    "chaos":      "Хаос (формат меняется per cut)",
    "custom":     "Custom (W/H ниже)",
}
# Reverse map for _apply_session
_ASPECT_LABELS_REV = {v: k for k, v in _ASPECT_LABELS.items()}


# Clip-to-clip transition style. Label → AutoEditSettings.transition_mode value.
_TRANSITION_MODE_LABELS = {
    "Crossfade (обычный)": "crossfade",
    "Pixel sort sweep":    "pixel_sort_sweep",
    "Datamosh freeze":     "datamosh_freeze",
    "RGB-split glitch":    "rgb_split_glitch",
    "VHS burst":           "vhs_burst",
    "Random (микс)":       "random",
}
_TRANSITION_MODE_LABELS_REV = {v: k for k, v in _TRANSITION_MODE_LABELS.items()}


def _resolve_transition_mode() -> str:
    """Read combo selection → AutoEditSettings.transition_mode value."""
    label = dpg.get_value("transition_mode_combo")
    return _TRANSITION_MODE_LABELS.get(label, "random")


def _resolve_cuts_per_beat() -> float:
    """Read combo selection → float."""
    label = dpg.get_value("cuts_per_beat_combo")
    return float(_CUTS_PER_BEAT_LABELS.get(label, 1.0))


def _resolve_beat_remap() -> float:
    label = dpg.get_value("beat_remap_combo")
    return float(_BEAT_REMAP_LABELS.get(label, 0.0))


def _resolve_aspect_mode() -> str:
    label = dpg.get_value("aspect_mode_combo")
    return _ASPECT_LABELS_REV.get(label, "custom")


def _on_aspect_mode_change(_s=None, val=None):
    """Enable/disable the W/H sliders depending on aspect_mode."""
    mode = _resolve_aspect_mode()
    is_custom = (mode == "custom")
    for tag in ("width", "height"):
        if dpg.does_item_exist(tag):
            try:
                dpg.configure_item(tag, enabled=is_custom)
            except Exception:
                pass


# Helpers to set the discrete combos from a numeric value (for session load).
def _snap_to_label(value: float, table: dict) -> str:
    """Return the table label closest to `value`."""
    best = None
    best_d = 1e9
    for label, v in table.items():
        d = abs(v - float(value))
        if d < best_d:
            best_d = d
            best = label
    return best or next(iter(table.keys()))


def _collect_settings() -> AutoEditSettings:
    return AutoEditSettings(
        duration         = float(dpg.get_value("duration")),
        fps              = int(dpg.get_value("fps")),
        output_w         = int(dpg.get_value("width")),
        output_h         = int(dpg.get_value("height")),
        cuts_per_beat    = _resolve_cuts_per_beat(),
        aspect_mode      = _resolve_aspect_mode(),
        beat_flash_ms    = int(dpg.get_value("flash_ms")),
        audio_offset_sec = float(dpg.get_value("offset")),
        override_bpm     = float(dpg.get_value("override_bpm")) if dpg.does_item_exist("override_bpm") else 0.0,
        crf              = int(dpg.get_value("crf")),
        palette          = _collect_palette(),
        palette_strength = float(dpg.get_value("palette_strength")),
        crossfade_ms     = int(dpg.get_value("crossfade_ms")),
        transition_mode  = _resolve_transition_mode(),
        seed                 = _ensure_seed(),
        clip_mode            = str(dpg.get_value("clip_mode_combo")),
        clip_stride          = int(dpg.get_value("clip_stride")),
        clip_avoid_recent    = int(dpg.get_value("clip_avoid_recent")),
        beat_remap_strength  = _resolve_beat_remap(),
        cam_stabilize        = bool(dpg.get_value("cam_stabilize")),
        random_clip_starts   = bool(dpg.get_value("random_clip_starts")),
        single_video_clips   = int(dpg.get_value("single_video_clips")),
        preview_scale        = float(dpg.get_value("preview_scale")),
        dither_params        = _collect_dither_params(),
        ascii_text           = _collect_ascii_text(),
        ascii_params         = _collect_ascii_params(),
        psort_params         = _collect_psort_params(),
        kaleido_params       = _collect_kaleido_params(),
        slit_params          = _collect_slit_params(),
        distort_params       = _collect_distort_params(),
        thermal_params       = _collect_thermal_params(),
        cellular_params      = _collect_cellular_params(),
        elastic_grid_params  = _collect_elastic_grid_params(),
        crt_monitor_params   = _collect_crt_monitor_params(),
        glitch_params        = _collect_glitch_params(),
        modulate_params      = _collect_modulate_params(),
        pixelate_params      = _collect_pixelate_params(),
        bypass_montage       = bool(dpg.get_value("bypass_montage_chk")) if dpg.does_item_exist("bypass_montage_chk") else False,
        effect_overrides     = _collect_effect_overrides(),
        text_cards           = _collect_text_cards(),
        blob_track           = _collect_blob_track(),
        mod_config           = _collect_mod_config(),
        effect_chain         = _collect_effect_chain(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# render / preview workers
# ═══════════════════════════════════════════════════════════════════════════════

def _start_preview(_s=None, _a=None):
    if not STATE["audio"]:
        dpg.set_value("status", "Выбери аудио файл"); return
    if not STATE["media"]:
        dpg.set_value("status", "Выбери папку с media"); return
    _autosave_last_session()
    s = _collect_settings()
    dpg.configure_item("preview_btn", enabled=False, label="Preview…")
    dpg.configure_item("render_btn",  enabled=False)

    def progress_cb(p, msg):
        try:
            dpg.set_value("progress", float(p))
            dpg.set_value("status",   str(msg))
        except Exception:
            pass

    def worker():
        try:
            automatch_preview(Path(STATE["audio"]), Path(STATE["media"]), s,
                              progress=progress_cb)
            dpg.set_value("status", "Preview закрыт")
        except Exception as e:
            dpg.set_value("status", f"Ошибка preview: {e}")
        finally:
            dpg.configure_item("preview_btn", enabled=True, label="Preview")
            dpg.configure_item("render_btn",  enabled=True)

    threading.Thread(target=worker, daemon=True).start()


def _start_render(_s=None, _a=None):
    if not STATE["audio"]:
        dpg.set_value("status", "Выбери аудио файл"); return
    if not STATE["media"]:
        dpg.set_value("status", "Выбери папку с media"); return
    _autosave_last_session()
    s = _collect_settings()

    # Auto-suffix so we don't overwrite previous renders.
    out_path = _next_indexed_path(Path(STATE["output"]))

    # Reset cancel signal + show Stop
    _render_cancel.clear()
    dpg.configure_item("render_btn",  enabled=False, label="Rendering…")
    dpg.configure_item("preview_btn", enabled=False)
    if dpg.does_item_exist("stop_btn"):
        dpg.configure_item("stop_btn", enabled=True, label="Stop", show=True)
    dpg.set_value("progress", 0.0)

    def progress_cb(p, msg):
        try:
            dpg.set_value("progress", float(p))
            dpg.set_value("status",   str(msg))
        except Exception:
            pass

    def worker():
        try:
            out = automatch_run(Path(STATE["audio"]), Path(STATE["media"]),
                                out_path, s,
                                progress=progress_cb,
                                cancel=_render_cancel)
            dpg.set_value("status", f"Готово → {out}")
        except RuntimeError as e:
            if "cancelled" in str(e):
                dpg.set_value("status", "Рендер отменён")
            else:
                dpg.set_value("status", f"Ошибка: {e}")
        except Exception as e:
            dpg.set_value("status", f"Ошибка: {e}")
        finally:
            dpg.configure_item("render_btn",  enabled=True, label="Render")
            dpg.configure_item("preview_btn", enabled=True)
            if dpg.does_item_exist("stop_btn"):
                dpg.configure_item("stop_btn", enabled=False, label="Stop", show=False)

    threading.Thread(target=worker, daemon=True).start()


# ─── batch render: N variants of the same audio segment ─────────────────────

# Palettes that look distinct from one another — used when "vary palette" is on
_BATCH_PALETTES = ("auto", "cyber_noir", "dreamcore", "gits", "warm",
                   "cold", "mono", "infrared", "vapor")


def _make_variant_settings(base: AutoEditSettings, variant_idx: int,
                            vary_style: bool, rng) -> AutoEditSettings:
    """Build the settings for one batch variant.  Each variant always gets a
    fresh seed; if `vary_style` is True we also rotate palette, clip-order
    and a few timing knobs so the 10 outputs feel genuinely different —
    not just shuffled clip orders.
    """
    seed = rng.randint(1, 2_000_000_000)
    s = dataclass_replace(base, seed=seed)
    # deep-copy the nested dataclasses + dicts so mutations don't leak
    s.text_cards       = deepcopy(base.text_cards)
    s.blob_track       = deepcopy(base.blob_track)
    s.effect_overrides = deepcopy(base.effect_overrides)
    s.dither_params    = deepcopy(base.dither_params)
    if not vary_style:
        return s

    # ── randomise the parts that determine the look ──
    s.palette             = rng.choice(_BATCH_PALETTES)
    s.palette_strength    = round(rng.uniform(0.20, 0.55), 2)
    s.clip_mode           = rng.choice(list(CLIP_MODES))
    s.beat_remap_strength = round(rng.uniform(0.0, 0.7), 2)
    # cuts/beat: cycle a useful set — half, normal, double
    s.cuts_per_beat       = rng.choice([0.5, 1.0, 1.0, 2.0])      # bias to 1.0
    # crossfade time: short for snappy variants, longer for dreamy ones
    s.crossfade_ms        = rng.choice([120, 220, 320, 480])
    return s


def _variant_output(base_out: Path, idx: int, total: int) -> Path:
    """Produce a per-variant output path: `recordings/track_03.mp4`."""
    stem = base_out.stem
    width = max(2, len(str(total)))
    suffix = base_out.suffix if base_out.suffix else ".mp4"
    return base_out.parent / f"{stem}_{idx:0{width}d}{suffix}"


# ─── Single-video slice render: pure cuts or with random effects ─────────

def _start_slice_render(clean: bool):
    """Render a beat-sync slice montage from the currently-selected single
    video file.  Two flavours:
        clean=True  → all effects disabled, just cuts + (no) transitions
        clean=False → all effects in 'random' state with random mix
    The selected media must already be a single video file (use the
    'Файл...' picker); folder mode is rejected.
    """
    if not STATE.get("audio"):
        dpg.set_value("status", "Выбери аудио файл"); return
    if not STATE.get("media"):
        dpg.set_value("status", "Выбери ВИДЕО-файл (кнопка Файл...)"); return
    p = Path(STATE["media"])
    if not (p.is_file() and p.suffix.lower() in _SINGLE_VIDEO_EXTS):
        dpg.set_value("status",
            "Нарезка требует один видео-файл — выбери его кнопкой 'Файл...'"); return

    _autosave_last_session()
    base = _collect_settings()
    # New settings clone with our slice tweaks applied
    s = dataclass_replace(base)
    s.effect_overrides = deepcopy(base.effect_overrides)
    s.text_cards       = deepcopy(base.text_cards)
    s.blob_track       = deepcopy(base.blob_track)

    if clean:
        # ── disable everything: pure beat-sync cuts only ──
        s.effect_overrides = {
            k: {"state": "off", "mix": 0.0, "random_mix": False}
            for k in USER_EFFECTS
        }
        # also kill text cards + blob detection
        try: s.text_cards.enabled = False
        except Exception: pass
        try: s.blob_track.enabled = False
        except Exception: pass
        # short crossfade so cuts feel deliberate, not blurred
        s.crossfade_ms = 80
        label_kind = "clean"
    else:
        # ── all effects on RND with high mix ──
        s.effect_overrides = {
            k: {"state": "random", "mix": 0.75, "random_mix": True}
            for k in USER_EFFECTS
        }
        label_kind = "fx"

    base_out  = Path(STATE["output"])
    raw_out   = base_out.parent / f"{base_out.stem}_slice_{label_kind}{base_out.suffix or '.mp4'}"
    out_path  = _next_indexed_path(raw_out)

    _render_cancel.clear()
    dpg.configure_item("slice_clean_btn",  enabled=False)
    dpg.configure_item("slice_fx_btn",     enabled=False)
    dpg.configure_item("render_btn",       enabled=False)
    dpg.configure_item("preview_btn",      enabled=False)
    if dpg.does_item_exist("stop_btn"):
        dpg.configure_item("stop_btn", enabled=True, label="Stop", show=True)
    dpg.set_value("progress", 0.0)

    def progress_cb(p_, msg):
        try:
            dpg.set_value("progress", float(p_))
            dpg.set_value("status", f"[slice/{label_kind}] {msg}")
        except Exception: pass

    def worker():
        try:
            out = automatch_run(Path(STATE["audio"]), Path(STATE["media"]),
                                out_path, s,
                                progress=progress_cb,
                                cancel=_render_cancel)
            dpg.set_value("status", f"Готово → {out}")
        except RuntimeError as e:
            if "cancelled" in str(e):
                dpg.set_value("status", "Нарезка отменена")
            else:
                dpg.set_value("status", f"Ошибка нарезки: {e}")
        except Exception as e:
            dpg.set_value("status", f"Ошибка нарезки: {e}")
            import traceback; traceback.print_exc()
        finally:
            for tag in ("slice_clean_btn", "slice_fx_btn",
                        "render_btn", "preview_btn"):
                try: dpg.configure_item(tag, enabled=True)
                except Exception: pass
            if dpg.does_item_exist("stop_btn"):
                dpg.configure_item("stop_btn", enabled=False, label="Stop", show=False)

    threading.Thread(target=worker, daemon=True).start()


def _start_slice_clean(_s=None, _a=None):  _start_slice_render(clean=True)
def _start_slice_fx(_s=None, _a=None):     _start_slice_render(clean=False)


def _start_batch_render(_s=None, _a=None):
    """Render N distinct variants of the current AutoMatch project.

    Every variant gets a fresh seed; if "вариативный стиль" is enabled it
    also gets a randomised palette / clip-order / beat-remap, producing 10
    visually distinct outputs from the same audio + media folder.
    """
    if not STATE["audio"]:
        dpg.set_value("status", "Выбери аудио файл"); return
    if not STATE["media"]:
        dpg.set_value("status", "Выбери папку с media"); return

    _autosave_last_session()

    n          = int(dpg.get_value("batch_count"))
    vary_style = bool(dpg.get_value("batch_vary_style"))
    base       = _collect_settings()
    # Auto-pick a free batch base (e.g. automatch_5.mp4) so re-running
    # batch doesn't overwrite a previous one.
    base_out   = _next_indexed_path(Path(STATE["output"]))
    base_seed_widget = int(dpg.get_value("seed_input"))
    rng = random.Random(base_seed_widget if base_seed_widget > 0 else None)

    _render_cancel.clear()
    dpg.configure_item("batch_btn",  enabled=False, label="Batch…")
    dpg.configure_item("render_btn", enabled=False)
    dpg.configure_item("preview_btn", enabled=False)
    if dpg.does_item_exist("stop_btn"):
        dpg.configure_item("stop_btn", enabled=True, label="Stop", show=True)
    dpg.set_value("progress", 0.0)

    def worker():
        outputs: list = []
        try:
            for i in range(n):
                if _render_cancel.is_set():
                    raise RuntimeError("cancelled")
                vs = _make_variant_settings(base, i, vary_style, rng)
                out_path = _variant_output(base_out, i + 1, n)
                tag = (f"[{i+1}/{n}]  seed={vs.seed}  "
                       f"palette={vs.palette!r}  clip={vs.clip_mode}  "
                       f"remap={vs.beat_remap_strength:.2f}")

                def progress_cb(p, msg, _vi=i, _tag=tag):
                    try:
                        overall = (_vi + float(p)) / max(n, 1)
                        dpg.set_value("progress", overall)
                        dpg.set_value("status", f"{_tag}  {msg}")
                    except Exception:
                        pass

                out = automatch_run(Path(STATE["audio"]),
                                    Path(STATE["media"]),
                                    out_path, vs,
                                    progress=progress_cb,
                                    cancel=_render_cancel)
                outputs.append(str(out))
            dpg.set_value("progress", 1.0)
            dpg.set_value("status",
                f"Готово! {n} вариантов в {base_out.parent}")
            print(f"[automatch_gui] batch complete:")
            for p in outputs:
                print(f"   {p}")
        except RuntimeError as e:
            if "cancelled" in str(e):
                dpg.set_value("status",
                    f"Batch отменён ({len(outputs)}/{n} готово)")
            else:
                dpg.set_value("status", f"Ошибка batch: {e}")
        except Exception as e:
            dpg.set_value("status", f"Ошибка batch: {e}")
            import traceback; traceback.print_exc()
        finally:
            dpg.configure_item("batch_btn",  enabled=True,
                               label=f"Сделать {n} видео")
            dpg.configure_item("render_btn", enabled=True)
            dpg.configure_item("preview_btn", enabled=True)
            if dpg.does_item_exist("stop_btn"):
                dpg.configure_item("stop_btn", enabled=False, label="Stop", show=False)

    threading.Thread(target=worker, daemon=True).start()


def _on_batch_count_change(_s, val):
    """Update the batch button label when the count changes."""
    try:
        dpg.configure_item("batch_btn", label=f"Сделать {int(val)} видео")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# sub-panel collectors
# ═══════════════════════════════════════════════════════════════════════════════

def _collect_text_cards() -> TextCardSettings:
    def _g(t, d):
        return dpg.get_value(t) if dpg.does_item_exist(t) else d
    def _col(t, d):
        v = dpg.get_value(t) if dpg.does_item_exist(t) else None
        return (int(v[0]), int(v[1]), int(v[2])) if v else d

    lines = [l.strip() for l in str(_g("tc_lines","")).splitlines() if l.strip()]
    return TextCardSettings(
        enabled     = bool(_g("tc_enabled", False)),
        probability = float(_g("tc_prob",   0.40)),
        duration    = float(_g("tc_dur",    0.80)),
        vibe        = str(_g("tc_vibe",     "")),
        lines       = lines,
        color       = _col("tc_color",    (255, 255, 255)),
        bg_color    = _col("tc_bg_color", (0,   0,   0)),
        bg_alpha    = float(_g("tc_bg_alpha",  1.0)),
        font_scale  = float(_g("tc_font_scale",1.0)),
        align       = str(_g("tc_align",  "center")),
        font_path   = _font_label_to_path(str(_g("tc_font", _FONT_DEFAULT_LABEL))),
    )


def _apply_text_cards(d: dict) -> None:
    """Push every saved text-card field back into its widget.

    Without this the whole text-card section reverts to defaults on every
    session reload (only the font picker used to be restored), so users saw
    text cards silently disabled after relaunch.
    """
    if not d:
        return
    def _set(tag, val):
        if dpg.does_item_exist(tag):
            try: dpg.set_value(tag, val)
            except Exception: pass
    _set("tc_enabled",    bool(d.get("enabled", False)))
    _set("tc_prob",       float(d.get("probability", 0.40)))
    _set("tc_dur",        float(d.get("duration",    0.80)))
    # lines: stored list → newline-joined text in the multiline box
    lines = d.get("lines") or []
    if isinstance(lines, (list, tuple)):
        _set("tc_lines", "\n".join(str(l) for l in lines))
    elif isinstance(lines, str):
        _set("tc_lines", lines)
    _set("tc_bg_alpha",   float(d.get("bg_alpha",   1.0)))
    _set("tc_font_scale", float(d.get("font_scale", 1.0)))
    al = str(d.get("align", "center"))
    _set("tc_align", al if al in _ALIGN_OPTS else "center")
    col = d.get("color")
    if col and len(col) >= 3:
        _set("tc_color", [int(col[0]), int(col[1]), int(col[2]), 255])
    bg = d.get("bg_color")
    if bg and len(bg) >= 3:
        _set("tc_bg_color", [int(bg[0]), int(bg[1]), int(bg[2]), 255])
    # vibe last → fire its callback so the vibe preview text updates too
    vibe = str(d.get("vibe", ""))
    if dpg.does_item_exist("tc_vibe") and vibe in _VIBE_KEYS:
        dpg.set_value("tc_vibe", vibe)
        try: _on_tc_vibe_change(None, vibe)
        except Exception: pass


_HUE_PRESETS = {"Red":0,"Orange":10,"Yellow":30,"Green":60,
                "Cyan":100,"Blue":120,"Purple":140,"Pink":160}


def _collect_blob_track() -> BlobTrackSettings:
    def _g(t, d):
        return dpg.get_value(t) if dpg.does_item_exist(t) else d
    def _col(t, d):
        v = dpg.get_value(t) if dpg.does_item_exist(t) else None
        return (int(v[0]), int(v[1]), int(v[2])) if v else d

    # tracker mode (display label → key)
    tm_label = _g("bt_tracker_mode_combo", _BLOB_TRACKER_MODES[0])
    try:
        tm_idx = _BLOB_TRACKER_MODES.index(tm_label)
    except ValueError:
        tm_idx = 0
    tracker_mode = _BLOB_TRACKER_KEYS[tm_idx]

    # bracket marker style (display label → index)
    bs_label = _g("bt_bracket_style_combo", _BLOB_BRACKET_STYLES[0])
    try:
        bracket_style = _BLOB_BRACKET_STYLES.index(bs_label)
    except ValueError:
        bracket_style = 0

    # HUD timestamp format (display label → index)
    ts_label = _g("bt_ts_format_combo", _BLOB_TS_FORMATS[0])
    try:
        timestamp_format = _BLOB_TS_FORMATS.index(ts_label)
    except ValueError:
        timestamp_format = 0

    return BlobTrackSettings(
        enabled           = bool(_g("bt_enabled", False)),
        tracker_mode      = tracker_mode,
        hue_center        = int(_g("bt_hue",      120)),
        hue_range         = int(_g("bt_hue_range",  30)),
        min_val           = int(_g("bt_min_val",    80)),
        min_sat           = int(_g("bt_min_sat",    50)),
        min_size          = int(_g("bt_min_size",  800)),
        max_blobs         = int(_g("bt_max_blobs",   3)),
        show_overlay      = bool(_g("bt_overlay",  True)),
        bracket_color     = _col("bt_col", (80, 255, 140)),
        bracket_thickness = int(_g("bt_thickness",   2)),
        show_labels       = bool(_g("bt_labels",   True)),
        show_centroid     = bool(_g("bt_centroid", True)),
        show_data_line    = bool(_g("bt_dataline", True)),
        zoom_to_blob      = bool(_g("bt_zoom",    False)),
        # ── visual extras ──────────────────────────────────────────────
        bracket_style     = bracket_style,
        show_crosshair    = bool(_g("bt_crosshair", False)),
        show_leader       = bool(_g("bt_leader",    False)),
        show_trails       = bool(_g("bt_trails",    False)),
        trail_length      = int(_g("bt_trail_len",     12)),
        show_network      = bool(_g("bt_network",   False)),
        label_prefix      = str(_g("bt_label_prefix", "TGT")),
        show_extra_data   = bool(_g("bt_extra_data", False)),
        show_timestamp    = bool(_g("bt_timestamp", False)),
        timestamp_format  = timestamp_format,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# theme
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Deep-navy "expensive" palette ────────────────────────────────────────
# Kept variable names (_TEAL etc.) so the rest of the file doesn't need to
# rename — only the values changed.  Colours follow a coherent blue ramp:
#   _BG       canvas behind everything (near-black, hint of blue)
#   _PANEL    section panels
#   _FRAME    interactive frames (sliders, combos, inputs)
#   _FRAME_H  hovered frame
#   _TEAL     primary accent — bright cool sapphire blue
#   _TEAL2    secondary accent for active state
#   _DIM      muted secondary text
#   _TEXT     body text — slightly warm off-white for less eye strain
_BG       = (8,   8,  12)        # GL1TCH near-black
_PANEL    = (16, 16,  22)        # section panel — slight lift
_FRAME    = (24, 24,  32)        # interactive frame bg
_FRAME_H  = (38, 38,  52)        # hovered
_TEAL     = (  0, 216, 212)      # GL1TCH cyan (accent slot)
_TEAL2    = ( 88, 240, 235)      # bright cyan hover
_DIM      = (110, 120, 138)      # muted text
_TEXT     = (220, 224, 232)      # body text


def _apply_theme():
    """GL1TCH theme — near-black panels, cyan/magenta/green accents, flat
    pixel-edge frames (zero rounding).  Colours align with the matrix:
        cyan   = primary accent (sliders, focus, locked cells)
        magenta= section dividers, headers, RND cells
        green  = positive/active (ON cells, checkmark)
    """
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            # ── surfaces ──
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,           _BG)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,            _PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,            (14, 14, 20))
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg,          _PANEL)
            # ── frames (sliders, combos, inputs) ──
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,            _FRAME)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,     _FRAME_H)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,      (52, 52, 72))
            # ── title bars ──
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,            _GL_MAGENTA)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,      _GL_MAGENTA)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgCollapsed,   (140, 18, 84))
            # ── buttons ──
            dpg.add_theme_color(dpg.mvThemeCol_Button,             (28, 28, 40))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,      (52, 52, 78))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,       _TEAL)
            # ── headers (collapsing sections) ──
            dpg.add_theme_color(dpg.mvThemeCol_Header,             (60, 14, 40))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,      (90, 22, 60))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,       _GL_MAGENTA)
            # ── sliders & checkmarks ──
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab,         _TEAL)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive,   _TEAL2)
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark,          _GL_GREEN)
            # ── text ──
            dpg.add_theme_color(dpg.mvThemeCol_Text,               _TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,       _DIM)
            dpg.add_theme_color(dpg.mvThemeCol_TextSelectedBg,     (0, 216, 212, 120))
            # ── separators / borders ──
            dpg.add_theme_color(dpg.mvThemeCol_Separator,          _GL_MAGENTA)
            dpg.add_theme_color(dpg.mvThemeCol_SeparatorHovered,   (255, 80, 180))
            dpg.add_theme_color(dpg.mvThemeCol_SeparatorActive,    _TEAL)
            dpg.add_theme_color(dpg.mvThemeCol_Border,             (60, 22, 50))
            dpg.add_theme_color(dpg.mvThemeCol_BorderShadow,       (0, 0, 0, 0))
            # ── tables ──
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg,      (40, 12, 32))
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight,   (60, 22, 50))
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong,  _GL_MAGENTA)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg,         (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt,      (255, 255, 255, 9))
            # ── scrollbars ──
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,        _BG)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,      (60, 22, 50))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered,(110, 32, 80))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive,_GL_MAGENTA)
            # ── focus ring ──
            dpg.add_theme_color(dpg.mvThemeCol_NavHighlight,       _TEAL)
            # ── tabs ──
            dpg.add_theme_color(dpg.mvThemeCol_Tab,                (28, 28, 40))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,         (60, 14, 40))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,          _GL_MAGENTA)
            # ── styles: FLAT pixel edges, generous spacing ──
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,      0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,     0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,      0)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding,      0)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,        0)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,       0)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding,  0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize,    1)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize,   0)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,      14, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,       8,  5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,        8,  6)
            dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing,   6,  4)
            dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing,      18)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize,      12)
            dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize,        14)
    dpg.bind_theme(theme)


def _load_font(size: int = 14):
    """Load a chunky monospace font for the GL1TCH look.  Consolas is the
    only widely-available pixel-feel monospace on Windows that ships
    everywhere and has full Cyrillic coverage.  Falls back to Courier New
    then sans-serif if missing.
    """
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",     # Consolas regular
        r"C:\Windows\Fonts\consolab.ttf",    # Consolas bold (chunkier)
        r"C:\Windows\Fonts\cour.ttf",        # Courier New
        r"C:\Windows\Fonts\segoeui.ttf",     # last-resort sans
    ]
    chosen = next((p for p in candidates if os.path.exists(p)), None)
    if chosen is None:
        return None
    with dpg.font_registry():
        with dpg.font(chosen, size) as f:
            # Cyrillic + ASCII-safe icon glyphs.  In modern DPG these
            # hint/range calls are no-ops (auto), but harmless.
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Cyrillic)
            dpg.add_font_range(0x2190, 0x21FF)        # arrows
            dpg.add_font_range(0x2500, 0x257F)        # box drawing
            dpg.add_font_range(0x25A0, 0x25FF)        # geometric shapes
            dpg.add_font_range(0x2600, 0x26FF)        # misc symbols
            dpg.add_font_range(0x2713, 0x2716)        # check / cross
    return f


# ═══════════════════════════════════════════════════════════════════════════════
# section builders
# ═══════════════════════════════════════════════════════════════════════════════

def _section_sep(label: str):
    """GL1TCH-style section heading: magenta bracketed title + Bayer-dither
    strip in place of the boring solid separator."""
    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        dpg.add_text("//", color=_GL_MAGENTA)
        dpg.add_text(label.upper(), color=_TEXT)
    if _TEX_DITHER_SEP:
        dpg.add_image(_TEX_DITHER_SEP, width=-1, height=2)
    else:
        dpg.add_separator()
    dpg.add_spacer(height=2)


def _build_files_section():
    _section_sep("Файлы")
    # Audio + Output rows (single picker each)
    for lbl, tag, default, cb in [
        ("Аудио",  "audio_label",  "не выбрано",     _pick_audio),
        ("Output", "output_label", STATE["output"],  _pick_output),
    ]:
        with dpg.group(horizontal=True):
            dpg.add_text(f"{lbl}:", color=_DIM)
            dpg.add_spacer(width=max(0, 56 - len(lbl) * 7))
            dpg.add_text(default, tag=tag, color=(145, 150, 165), wrap=560)
            dpg.add_spacer(width=6)
            dpg.add_button(label="…", width=24, height=22, callback=cb)
        dpg.add_spacer(height=2)

    # Media row: two pickers (folder OR single video) + segment count
    with dpg.group(horizontal=True):
        dpg.add_text("Media:", color=_DIM)
        dpg.add_spacer(width=max(0, 56 - 5 * 7))
        dpg.add_text("не выбрана", tag="media_label",
                     color=(145, 150, 165), wrap=520)
        dpg.add_spacer(width=6)
        dpg.add_button(label="Папка...", width=80, height=22,
                       callback=_pick_media)
        dpg.add_button(label="Файл...", width=72, height=22,
                       callback=_pick_single_video)
    with dpg.group(horizontal=True):
        dpg.add_spacer(width=66)
        dpg.add_text("Папка = много клипов  ·  Файл = один видео-файл (нарезка)",
                     color=_DIM)
    # Segments slider — used only when Media is a single video
    with dpg.group(horizontal=True):
        dpg.add_spacer(width=66)
        dpg.add_slider_int(
            label="Сегментов из одного видео",
            tag="single_video_clips",
            default_value=8, min_value=2, max_value=32,
            width=240)
    dpg.add_spacer(height=2)


_DITHER_MIX_MODES = ("avg", "multiply", "xor", "cross-mod")
_DITHER_COLOR_MODES = ("mono",  "per-channel", "duotone")
_DITHER_ALGOS = (
    "0 · Circuit modulation",
    "1 · Bayer 4×4 ordered",
    "2 · Bayer 8×8 ordered",
    "3 · Blue noise",
)

# Pixel sort: combo display labels.  Index = u_algorithm float value.
_PSORT_ALGO_LABELS = (
    "0 · Kim Asendorf horizontal",
    "1 · Kim Asendorf vertical",
    "2 · Bitonic merge sort horizontal",
    "3 · Bitonic merge sort vertical",
    "4 · DV25 block scramble",
)
_PSORT_COLOR_MODES = ("luma", "red", "green", "blue", "saturation")


def _build_effects_section():
    """GL1TCH-style effects matrix + side params panel.

    Layout:
        ┌─────────── matrix grid (4 × N) ───────────┐  ┌── side panel ──┐
        │  [LUMA]  [DUO]  [DUAL]  [GRAIN]            │  │ name [STATE]   │
        │  [TRAIL] [WAVE] [PSORT] [DMSH]             │  │ Mix  ───────── │
        │  [VHS]   [ASCII][CRT]   [DTHR]             │  │ ☐ Random mix   │
        └────────────────────────────────────────────┘  │  (dither extra)│
                                                       └────────────────┘
    Cell state colour:
        OFF=grey  ON=neon-green  RND=magenta  LCK=cyan
    Click = cycle state + select for side panel.
    """
    _section_sep("Эффекты")

    # quick actions
    with dpg.group(horizontal=True):
        dpg.add_text(
            "Клик по ячейке — открыть меню (параметры справа).  "
            "Повторный клик по выбранной: OFF → ON → RND → LCK → OFF.",
            color=_GL_DIM,
        )
        dpg.add_spacer(width=8)
        dpg.add_button(label="RND ALL", tag="fx_rnd_all_btn",
                       width=90, height=24,
                       callback=_randomize_all_effects)
        dpg.add_button(label="CLEAR",   tag="fx_clear_btn",
                       width=78, height=24,
                       callback=_reset_all_effects)
        dpg.add_button(label="KILL ALL", tag="fx_kill_all_btn",
                       width=88, height=24,
                       callback=_kill_all_effects)
        dpg.add_button(label="BROWSER", tag="fx_browser_btn",
                       width=80, height=24,
                       callback=lambda: dpg.configure_item("fx_browser_win", show=True))
        dpg.add_spacer(width=16)
        dpg.add_checkbox(label="Цепочка", tag="chain_enabled",
                         default_value=False,
                         callback=lambda: _rebuild_matrix())
        dpg.add_button(label="↺", width=26, height=24,
                       callback=lambda: _chain_reset())
    dpg.add_spacer(height=6)

    # 2-col: matrix on the left, side panel on the right
    with dpg.group(horizontal=True):
        # ── LEFT: matrix grid ────────────────────────────────────────
        with dpg.child_window(width=380, height=290, border=True,
                              tag="fx_matrix_container",
                              no_scrollbar=True):
            pass  # filled by _rebuild_matrix() below

        # ── RIGHT: side params panel ─────────────────────────────────
        with dpg.child_window(width=-1, height=290, border=True,
                              tag="fx_side_panel"):
            dpg.add_text("— ничего не выбрано —", tag="fx_side_name",
                         color=_GL_DIM)
            dpg.add_text(
                "Клик по ячейке матрицы — параметры этого эффекта появятся здесь.",
                tag="fx_side_hint", color=_GL_DIM, wrap=-1,
            )

            dpg.add_slider_float(
                label="Mix", tag="fx_side_mix",
                default_value=0.65, min_value=0.0, max_value=1.0,
                width=-1, format="%.2f", show=False,
                callback=_on_side_mix_change,
            )
            dpg.add_checkbox(
                label="Random mix (per cut)", tag="fx_side_rnd",
                default_value=False, show=False,
                callback=_on_side_rnd_change,
            )
            # Compositing mode (TouchDesigner Composite TOP). Shown only for
            # effects whose shader honours u_blend (FM + CRT, for now).
            dpg.add_combo(
                items=list(_BLEND_MODES), default_value=_BLEND_MODES[0],
                label="Наложение", tag="fx_side_blend",
                width=-1, show=False, callback=_on_side_blend_change,
            )

            # Pixel sort sub-panel — algorithm picker + key controls
            with dpg.group(tag="fx_side_psort", show=False):
                dpg.add_separator()
                dpg.add_text("Pixel sort algorithm", color=_GL_MAGENTA)
                dpg.add_combo(
                    items=list(_PSORT_ALGO_LABELS),
                    default_value=_PSORT_ALGO_LABELS[0],
                    tag="am_psort_algo_combo",
                    label="Algo", width=240,
                )
                dpg.add_text(
                    "0/1: линейный Kim-Asendorf (рваные полосы по порогу).  "
                    "2/3: bitonic merge sort (ровные градиентные полосы).  "
                    "4: DV25 block scramble (единственный блочный).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_spacer(height=2)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Threshold", tag="am_psort_threshold",
                                         default_value=0.00, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Размер", tag="am_psort_length",
                                         default_value=0.40, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                dpg.add_text(
                    "Размер: для сортировок — длина полосы (8..96 px), "
                    "для block scramble — размер блока (4..28 px).  "
                    "Threshold=0 → сортирует весь кадр; выше → только яркие "
                    "участки (рваные куски).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(
                    items=list(_PSORT_COLOR_MODES),
                    default_value=_PSORT_COLOR_MODES[0],
                    tag="am_psort_color_mode_combo",
                    label="Feature", width=180,
                )

            # Kaleidoscope sub-panel — segments / rotation / spin / zoom
            with dpg.group(tag="fx_side_kaleido", show=False):
                dpg.add_separator()
                dpg.add_text("Kaleidoscope", color=_GL_MAGENTA)
                dpg.add_text(
                    "Полярная складка кадра в N зеркальных секторов вокруг центра.",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_spacer(height=2)
                with dpg.group(horizontal=True):
                    dpg.add_slider_int(label="Сектора", tag="am_kaleido_sides",
                                       default_value=6, min_value=2, max_value=16,
                                       width=180)
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Зум", tag="am_kaleido_zoom",
                                         default_value=1.0, min_value=0.3, max_value=3.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Поворот", tag="am_kaleido_angle",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Вращение/с", tag="am_kaleido_spin",
                                         default_value=0.10, min_value=-1.0, max_value=1.0,
                                         width=180, format="%.2f")

            # Slit-scan sub-panel — direction / speed / capture line / feedback
            with dpg.group(tag="fx_side_slit", show=False):
                dpg.add_separator()
                dpg.add_text("Slit-scan", color=_GL_MAGENTA)
                dpg.add_text(
                    "Временной смаз: строки/колонки прокручиваются, в край "
                    "впрыскивается живая скан-линия. Каждая линия — свой момент.",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(
                    items=list(_SLIT_DIR_LABELS),
                    default_value=_SLIT_DIR_LABELS[0],
                    tag="am_slit_dir_combo",
                    label="Направление", width=260,
                )
                dpg.add_spacer(height=2)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Скорость", tag="am_slit_speed",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Скан-линия", tag="am_slit_scanpos",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Persistence", tag="am_slit_feedback",
                                         default_value=1.0, min_value=0.5, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Порог (threshold)", tag="am_slit_threshold",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                dpg.add_text(
                    "Persistence < 1 → старые линии постепенно темнеют.\n"
                    "Порог > 0 → яркостный гейт на скан-линии: тёмное обрезается "
                    "в чёрный, остаются только яркие штрихи.",
                    color=_GL_DIM, wrap=-1,
                )

            # Distort sub-panel — procedural displacement-map warp
            with dpg.group(tag="fx_side_distort", show=False):
                dpg.add_separator()
                dpg.add_text("Distort (карта смещения)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Процедурная карта (шум/fBM) генерится в шейдере и сдвигает "
                    "пиксели по X/Y. Карту можно предобработать — как картинку-"
                    "карту смещения в tooooools.",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(_DISTORT_NOISE_MODES, label="Тип карты",
                              tag="am_distort_noise_mode",
                              default_value=_DISTORT_NOISE_MODES[0], width=200)
                dpg.add_text("сдвиг", color=_GL_DIM)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="X Shift", tag="am_distort_xshift",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Y Shift", tag="am_distort_yshift",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Порог", tag="am_distort_threshold",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Масштаб карты", tag="am_distort_scale",
                                         default_value=4.0, min_value=0.5, max_value=24.0,
                                         width=180, format="%.1f")
                dpg.add_slider_float(label="Анимация (скорость)", tag="am_distort_speed",
                                     default_value=0.3, min_value=0.0, max_value=2.0,
                                     width=180, format="%.2f")
                dpg.add_spacer(height=2)
                dpg.add_text("предобработка карты", color=_GL_DIM)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Blur", tag="am_distort_blur",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Grain", tag="am_distort_grain",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Gamma", tag="am_distort_gamma",
                                         default_value=1.0, min_value=0.2, max_value=4.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Black pt", tag="am_distort_black",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                dpg.add_slider_float(label="White pt", tag="am_distort_white",
                                     default_value=1.0, min_value=0.0, max_value=1.0,
                                     width=180, format="%.2f")
                dpg.add_text(
                    "Порог > 0 → смещаются только светлые области карты.\n"
                    "Black/White point растягивают контраст карты.",
                    color=_GL_DIM, wrap=-1,
                )

            # ── Thermal sub-panel — false-colour infrared LUT ──
            with dpg.group(tag="fx_side_thermal", show=False):
                dpg.add_separator()
                dpg.add_text("Thermal (тепловизор)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Яркость кадра раскрашивается по тепловой палитре. "
                    "Gain/Gamma тянут отклик, Levels квантует на полосы.",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(_THERMAL_PALETTES, label="Палитра",
                              tag="am_thermal_palette",
                              default_value=_THERMAL_PALETTES[0], width=200)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Gain", tag="am_thermal_gain",
                                         default_value=1.0, min_value=0.2, max_value=3.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Gamma", tag="am_thermal_gamma",
                                         default_value=1.0, min_value=0.2, max_value=4.0,
                                         width=180, format="%.2f")
                dpg.add_slider_float(label="Levels (полосы)", tag="am_thermal_levels",
                                     default_value=0.0, min_value=0.0, max_value=12.0,
                                     width=180, format="%.0f")

            # ── Cellular automata sub-panel (feedback) ──
            with dpg.group(tag="fx_side_cellular", show=False):
                dpg.add_separator()
                dpg.add_text("Cellular automata", color=_GL_MAGENTA)
                dpg.add_text(
                    "Живые клетки засеиваются по яркости и эволюционируют "
                    "от кадра к кадру (сброс на каждом кате).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(_CA_MODES, label="Режим", tag="am_ca_mode",
                              default_value=_CA_MODES[0], width=200)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Размер клетки", tag="am_ca_cell",
                                         default_value=8.0, min_value=2.0, max_value=32.0,
                                         width=180, format="%.0f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Скорость", tag="am_ca_rate",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                dpg.add_slider_float(label="Порог засева", tag="am_ca_seed_thr",
                                     default_value=0.6, min_value=0.0, max_value=1.0,
                                     width=180, format="%.2f")
                dpg.add_color_edit(label="Цвет клеток", tag="am_ca_tint",
                                   default_value=(79, 255, 140, 255),
                                   no_alpha=True, width=200)

            # ── Elastic grid sub-panel — mesh warp ──
            with dpg.group(tag="fx_side_elastic_grid", show=False):
                dpg.add_separator()
                dpg.add_text("Elastic grid (mesh warp)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Решётка узлов с анимированным смещением; пиксели тянутся "
                    "эластично (билинейный бленд узлов).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(_EG_NOISE_MODES, label="Движение узлов",
                              tag="am_eg_noise_mode",
                              default_value=_EG_NOISE_MODES[0], width=200)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Сетка (узлов)", tag="am_eg_grid",
                                         default_value=8.0, min_value=2.0, max_value=32.0,
                                         width=180, format="%.0f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Сила", tag="am_eg_strength",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Скорость", tag="am_eg_speed",
                                         default_value=0.5, min_value=0.0, max_value=2.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Джиттер", tag="am_eg_jitter",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")

            # ── CRT monitor v2 sub-panel — barrel + aperture grille ──
            with dpg.group(tag="fx_side_crt_monitor", show=False):
                dpg.add_separator()
                dpg.add_text("CRT монитор (v2)", color=_GL_MAGENTA)
                dpg.add_text(
                    "«Толстый» ламповый ТВ: бочка экрана, RGB-маска, "
                    "скан-линии, виньетка, свечение, краевая аберрация.",
                    color=_GL_DIM, wrap=-1,
                )
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Кривизна", tag="am_crtm_curvature",
                                         default_value=0.30, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Скан-линии", tag="am_crtm_scanline",
                                         default_value=0.50, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Маска (grille)", tag="am_crtm_grille",
                                         default_value=0.40, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Виньетка", tag="am_crtm_vignette",
                                         default_value=0.40, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Свечение", tag="am_crtm_bloom",
                                         default_value=0.30, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Аберрация", tag="am_crtm_aberration",
                                         default_value=0.50, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")

            # ── Glitch sub-panel (feedback block displacement) ──
            with dpg.group(tag="fx_side_glitch", show=False):
                dpg.add_separator()
                dpg.add_text("Glitch (блочный)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Блоки кадра рвутся и замораживаются из предыдущего "
                    "кадра (сброс на каждом кате).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_slider_float(label="Размер блока", tag="am_glitch_block_size",
                                     default_value=0.30, min_value=0.0, max_value=1.0,
                                     width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Частота сбоев", tag="am_glitch_drop_chance",
                                         default_value=0.30, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Заморозка", tag="am_glitch_repeat_chance",
                                         default_value=0.20, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")

            # ── Modulation sub-panel — AM/FM/phase signal ──
            with dpg.group(tag="fx_side_modulate", show=False):
                dpg.add_separator()
                dpg.add_text("Modulation (сигнал)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Картинка как сигнал на несущей синусоиде: AM (бегущие "
                    "полосы яркости), FM (волнистый сдвиг), Phase (хрома-смаз), "
                    "RGB-полосы (бегущие радужные вертикальные полосы).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(_MOD_MODES, label="Режим", tag="am_mod_mode",
                              default_value=_MOD_MODES[0], width=200)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Частота", tag="am_mod_freq",
                                         default_value=20.0, min_value=1.0, max_value=80.0,
                                         width=180, format="%.0f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Глубина", tag="am_mod_amount",
                                         default_value=0.5, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Скорость", tag="am_mod_speed",
                                         default_value=0.5, min_value=0.0, max_value=2.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Ось", tag="am_mod_axis",
                                         default_value=0.0, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")

            # ── Pixelate sub-panel — blocky pixelation + recursive feedback ──
            with dpg.group(tag="fx_side_pixelate", show=False):
                dpg.add_separator()
                dpg.add_text("Pixelate (фидбек)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Крупные пиксели прогоняются через рекурсивную петлю "
                    "(как Feedback TOP): прошлый кадр чуть масштабируется, "
                    "сдвигается и затухает → пиксели расползаются шлейфами "
                    "и медленно гаснут (pixel memory decay).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_combo(_PIXELATE_FB_MODES, label="Режим петли",
                              tag="am_pixelate_fb_mode",
                              default_value=_PIXELATE_FB_MODES[0], width=200)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Размер пикселя", tag="am_pixelate_block",
                                         default_value=12.0, min_value=1.0, max_value=64.0,
                                         width=180, format="%.0f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Фидбек", tag="am_pixelate_feedback",
                                         default_value=0.90, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Зум петли", tag="am_pixelate_zoom",
                                         default_value=0.55, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Снос", tag="am_pixelate_drift",
                                         default_value=0.30, min_value=0.0, max_value=1.0,
                                         width=180, format="%.2f")

            # ASCII-specific controls — built once, shown only when
            # ASCII is the selected cell
            with dpg.group(tag="fx_side_ascii", show=False):
                dpg.add_separator()
                dpg.add_text("ASCII glyphs", color=_GL_MAGENTA)
                dpg.add_text(
                    "Любая строка до 32 символов. Порядок: пусто/светлое слева → "
                    "плотное/тёмное справа. Атлас перепекается на лету.",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_input_text(
                    tag="am_ascii_text",
                    default_value=" .:+=#@█",
                    width=-1, hint="например: WORD ░▒▓█",
                )
                dpg.add_spacer(height=2)
                # presets the user can spam
                with dpg.group(horizontal=True):
                    dpg.add_text("Готовые:", color=_GL_DIM)
                    for label, text in (
                        ("blocks",  " .:+=#@█"),
                        ("hex",     "0123456789ABCDEF"),
                        ("morse",   " .-+=:|"),
                        ("retro",   " .,;:!*?#%@"),
                        ("waves",   " ▁▂▃▄▅▆▇█"),
                    ):
                        dpg.add_button(label=label, width=58, height=22,
                                       user_data=text,
                                       callback=_apply_ascii_preset)
                dpg.add_spacer(height=4)

                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Cell px (0..1 → 4..32)",
                                         tag="am_ascii_cell_size",
                                         default_value=0.30,
                                         min_value=0.0, max_value=1.0,
                                         width=200, format="%.2f")
                    dpg.add_spacer(width=8)
                    dpg.add_checkbox(label="Invert",
                                     tag="am_ascii_invert_chk",
                                     default_value=False)

                with dpg.group(horizontal=True):
                    dpg.add_checkbox(label="Цвет из источника",
                                     tag="am_ascii_color_source_chk",
                                     default_value=False)
                    dpg.add_spacer(width=8)
                    dpg.add_color_edit(label="Tint",
                                       tag="am_ascii_tint",
                                       default_value=(216, 255, 165, 255),
                                       no_alpha=True, no_inputs=True)

            # dither-specific controls — built once, shown only when
            # dither is the selected cell
            with dpg.group(tag="fx_side_dither", show=False):
                dpg.add_separator()
                dpg.add_text("Dither algorithm", color=_GL_MAGENTA)
                dpg.add_combo(
                    items=list(_DITHER_ALGOS),
                    default_value=_DITHER_ALGOS[0],
                    tag="am_dither_algo_combo",
                    label="Algo", width=240,
                )
                dpg.add_text(
                    "0: оригинальная circuit-modulation (oscillator-based).  "
                    "1-2: классический Bayer ordered.  "
                    "3: blue noise tile (64×64).",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_spacer(height=4)
                dpg.add_text("Common params (work для всех algos)", color=_GL_MAGENTA)
                dpg.add_text(
                    "Levels, color mode, color A/B, jitter, chromatic.",
                    color=_GL_DIM, wrap=-1,
                )
                dpg.add_spacer(height=2)

                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Levels", tag="am_dither_levels",
                                         default_value=6.0, min_value=2.0, max_value=32.0,
                                         width=170, format="%.0f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Grid px", tag="am_dither_grid_size",
                                         default_value=4.0, min_value=1.0, max_value=32.0,
                                         width=170, format="%.0f")

                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Osc A freq", tag="am_dither_osc_a_freq",
                                         default_value=15.0, min_value=0.5, max_value=50.0,
                                         width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Osc A speed", tag="am_dither_osc_a_speed",
                                         default_value=0.5, min_value=0.0, max_value=3.0,
                                         width=170, format="%.2f")

                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Osc B freq", tag="am_dither_osc_b_freq",
                                         default_value=12.0, min_value=0.5, max_value=50.0,
                                         width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Osc B speed", tag="am_dither_osc_b_speed",
                                         default_value=0.3, min_value=0.0, max_value=3.0,
                                         width=170, format="%.2f")

                with dpg.group(horizontal=True):
                    dpg.add_combo(items=list(_DITHER_MIX_MODES),
                                  default_value=_DITHER_MIX_MODES[0],
                                  tag="am_dither_mix_mode_combo",
                                  label="Mix mode", width=140)
                    dpg.add_spacer(width=6)
                    dpg.add_checkbox(label="Polarity (invert)",
                                     tag="am_dither_polarity_chk",
                                     default_value=False)

                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Trace thickness",
                                         tag="am_dither_trace_thickness",
                                         default_value=0.10, min_value=0.0, max_value=0.5,
                                         width=170, format="%.3f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Jitter",
                                         tag="am_dither_jitter",
                                         default_value=0.10, min_value=0.0, max_value=1.0,
                                         width=170, format="%.2f")
                dpg.add_slider_float(label="Chromatic phase",
                                     tag="am_dither_chromatic",
                                     default_value=0.0, min_value=0.0, max_value=1.0,
                                     width=170, format="%.2f")

                dpg.add_spacer(height=2)
                dpg.add_combo(items=list(_DITHER_COLOR_MODES),
                              default_value=_DITHER_COLOR_MODES[0],
                              tag="am_dither_color_mode_combo",
                              label="Color mode", width=140)
                with dpg.group(horizontal=True):
                    dpg.add_color_edit(label="Color A (shadow)",
                                       tag="am_dither_color_a",
                                       default_value=(5, 38, 25, 255),
                                       no_alpha=True, no_inputs=True)
                    dpg.add_spacer(width=6)
                    dpg.add_color_edit(label="Color B (highlight)",
                                       tag="am_dither_color_b",
                                       default_value=(140, 255, 114, 255),
                                       no_alpha=True, no_inputs=True)

            # ── VHS ──
            with dpg.group(tag="fx_side_vhs", show=False):
                dpg.add_separator()
                dpg.add_text("VHS", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Трекинг", tag="am_vhs_tracking_noise",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Джиттер строк", tag="am_vhs_line_jitter",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Хрома-блюр", tag="am_vhs_chroma_blur",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Потеря цвета", tag="am_vhs_color_loss",
                                         default_value=0.2, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_slider_float(label="Head-switch шум", tag="am_vhs_head_switch",
                                     default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── CRT ──
            with dpg.group(tag="fx_side_crt", show=False):
                dpg.add_separator()
                dpg.add_text("CRT scanlines", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Скан-линии", tag="am_crt_scanline",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Маска", tag="am_crt_mask",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Кривизна", tag="am_crt_curvature",
                                         default_value=0.2, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Виньетка", tag="am_crt_vignette",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_slider_float(label="Послесвечение", tag="am_crt_persistence",
                                     default_value=0.0, min_value=0.0, max_value=0.95, width=170, format="%.2f")

            # ── Wave Warp ──
            with dpg.group(tag="fx_side_wave_warp", show=False):
                dpg.add_separator()
                dpg.add_text("Wave Warp", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Амплитуда 1", tag="am_ww_amp1",
                                         default_value=0.03, min_value=0.0, max_value=0.2, width=170, format="%.3f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Частота 1", tag="am_ww_freq1",
                                         default_value=3.0, min_value=0.1, max_value=20.0, width=170, format="%.1f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Скорость 1", tag="am_ww_speed1",
                                         default_value=1.0, min_value=0.0, max_value=5.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Хром. аберр.", tag="am_ww_chromatic",
                                         default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Угол", tag="am_ww_angle",
                                         default_value=0.0, min_value=0.0, max_value=6.28, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_checkbox(label="Полярный режим", tag="am_ww_polar", default_value=False)

            # ── Datamosh ──
            with dpg.group(tag="fx_side_datamosh", show=False):
                dpg.add_separator()
                dpg.add_text("Datamosh", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Сила", tag="am_dm_strength",
                                         default_value=0.85, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Заморозка", tag="am_dm_freeze",
                                         default_value=0.45, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Период (с)", tag="am_dm_period",
                                         default_value=3.5, min_value=0.5, max_value=10.0, width=170, format="%.1f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Хаос", tag="am_dm_chaos",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_combo(items=["0 · Legacy P-frame", "1 · Optical-flow mosh"],
                              default_value="1 · Optical-flow mosh",
                              tag="am_dm_algo_combo", label="Algo", width=240)

            # ── Motion Trails ──
            with dpg.group(tag="fx_side_motion_trails", show=False):
                dpg.add_separator()
                dpg.add_text("Motion Trails", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Затухание", tag="am_mt_decay",
                                         default_value=0.85, min_value=0.0, max_value=0.99, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Порог", tag="am_mt_threshold",
                                         default_value=0.15, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Хаос", tag="am_mt_chaos",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Вторич. трейл", tag="am_mt_secondary",
                                         default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── RGB Split ──
            with dpg.group(tag="fx_side_rgb_split", show=False):
                dpg.add_separator()
                dpg.add_text("RGB Split", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Смещение", tag="am_rgb_offset",
                                         default_value=0.01, min_value=0.0, max_value=0.05, width=170, format="%.3f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Угол", tag="am_rgb_angle",
                                         default_value=0.0, min_value=0.0, max_value=6.28, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Волна", tag="am_rgb_wave_freq",
                                         default_value=0.0, min_value=0.0, max_value=10.0, width=170, format="%.1f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Блюр каналов", tag="am_rgb_blur",
                                         default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── Duotone ──
            with dpg.group(tag="fx_side_duotone", show=False):
                dpg.add_separator()
                dpg.add_text("Duotone", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Сила", tag="am_duo_strength",
                                         default_value=0.8, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Midpoint", tag="am_duo_midpoint",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_color_edit(label="Accent colour", tag="am_duo_accent",
                                   default_value=(164, 255, 90, 255), no_alpha=True, no_inputs=True)

            # ── Luma Key ──
            with dpg.group(tag="fx_side_luma_key", show=False):
                dpg.add_separator()
                dpg.add_text("Luma Key", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Lift", tag="am_lk_lift",
                                         default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Gain", tag="am_lk_gain",
                                         default_value=1.0, min_value=0.0, max_value=2.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Gamma", tag="am_lk_gamma",
                                         default_value=1.0, min_value=0.1, max_value=3.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_checkbox(label="Инверт", tag="am_lk_invert", default_value=False)

            # ── Edges ──
            with dpg.group(tag="fx_side_edges", show=False):
                dpg.add_separator()
                dpg.add_text("Edges (контуры)", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Порог", tag="am_ed_threshold",
                                         default_value=0.1, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Мягкость", tag="am_ed_soften",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_color_edit(label="Цвет контура", tag="am_ed_color",
                                   default_value=(164, 255, 90, 255), no_alpha=True, no_inputs=True)

            # ── Pixel Swap ──
            with dpg.group(tag="fx_side_pixel_swap", show=False):
                dpg.add_separator()
                dpg.add_text("Pixel Swap", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Размер блока", tag="am_ps_block_size",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Шанс", tag="am_ps_chance",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_slider_float(label="Дрейф", tag="am_ps_drift",
                                     default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── Found Footage ──
            with dpg.group(tag="fx_side_found_footage", show=False):
                dpg.add_separator()
                dpg.add_text("Found Footage", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Выпадения", tag="am_ff_dropout_density",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Сила выпад.", tag="am_ff_dropout_strength",
                                         default_value=0.7, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Варп строк", tag="am_ff_warp_amount",
                                         default_value=0.008, min_value=0.0, max_value=0.03, width=170, format="%.3f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Призрак", tag="am_ff_ghost_opacity",
                                         default_value=0.12, min_value=0.0, max_value=0.5, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Хрома-блид", tag="am_ff_chroma_bleed",
                                         default_value=2.5, min_value=0.0, max_value=8.0, width=170, format="%.1f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Luma crush", tag="am_ff_luma_crush",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── Chromatic Aberration ──
            with dpg.group(tag="fx_side_chromatic_aberration", show=False):
                dpg.add_separator()
                dpg.add_text("Хром. Аберрация", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Смещение", tag="am_ca_offset",
                                         default_value=0.012, min_value=0.0, max_value=0.05, width=170, format="%.3f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Радиальность", tag="am_ca_radial",
                                         default_value=0.8, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Угол (рад)", tag="am_ca_angle",
                                         default_value=0.0, min_value=0.0, max_value=6.28, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Спин", tag="am_ca_spin",
                                         default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── Color Posterize ──
            with dpg.group(tag="fx_side_color_posterize", show=False):
                dpg.add_separator()
                dpg.add_text("Постеризация", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Уровни", tag="am_post_levels",
                                         default_value=6.0, min_value=2.0, max_value=32.0, width=170, format="%.0f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Dither mix", tag="am_post_dither_mix",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_combo(items=["0 · Luma", "1 · Per-channel", "2 · Palette snap"],
                              default_value="0 · Luma",
                              tag="am_post_color_mode_combo", label="Режим", width=220)

            # ── Swirl ──
            with dpg.group(tag="fx_side_swirl", show=False):
                dpg.add_separator()
                dpg.add_text("Swirl (вихрь)", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Сила", tag="am_swirl_strength",
                                         default_value=3.0, min_value=0.0, max_value=10.0, width=170, format="%.1f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Радиус", tag="am_swirl_radius",
                                         default_value=0.5, min_value=0.1, max_value=1.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Центр X", tag="am_swirl_cx",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Центр Y", tag="am_swirl_cy",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_slider_float(label="Анимация", tag="am_swirl_animate",
                                     default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── Iridescent Foil ──
            with dpg.group(tag="fx_side_iridescent_foil", show=False):
                dpg.add_separator()
                dpg.add_text("Iridescent Foil", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Диапазон Hue", tag="am_ir_hue_range",
                                         default_value=0.5, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Скорость", tag="am_ir_speed",
                                         default_value=0.4, min_value=0.0, max_value=2.0, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Metallic", tag="am_ir_metallic",
                                         default_value=0.6, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Масштаб", tag="am_ir_scale",
                                         default_value=1.5, min_value=0.5, max_value=4.0, width=170, format="%.1f")
                dpg.add_slider_float(label="Прозрачность", tag="am_ir_opacity",
                                     default_value=0.7, min_value=0.0, max_value=1.0, width=170, format="%.2f")

            # ── Motion Blur ──
            with dpg.group(tag="fx_side_motion_blur", show=False):
                dpg.add_separator()
                dpg.add_text("Motion Blur", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Длина", tag="am_mb_length",
                                         default_value=0.012, min_value=0.0, max_value=0.03, width=170, format="%.3f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Угол (рад)", tag="am_mb_angle",
                                         default_value=0.0, min_value=0.0, max_value=6.28, width=170, format="%.2f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Радиальный", tag="am_mb_radial",
                                         default_value=0.0, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Сэмплов", tag="am_mb_samples",
                                         default_value=8.0, min_value=4.0, max_value=16.0, width=170, format="%.0f")

            # ── Advanced Glitch ──
            with dpg.group(tag="fx_side_advanced_glitch", show=False):
                dpg.add_separator()
                dpg.add_text("Advanced Glitch", color=_GL_MAGENTA)
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Размер блока", tag="am_ag_block_size",
                                         default_value=0.3, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Сдвиг", tag="am_ag_shift_amount",
                                         default_value=0.04, min_value=0.0, max_value=0.1, width=170, format="%.3f")
                with dpg.group(horizontal=True):
                    dpg.add_slider_float(label="Шанс глюка", tag="am_ag_chance",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                    dpg.add_spacer(width=6)
                    dpg.add_slider_float(label="Цвет сплит", tag="am_ag_color_shift",
                                         default_value=0.4, min_value=0.0, max_value=1.0, width=170, format="%.2f")
                dpg.add_slider_float(label="Скорость", tag="am_ag_speed",
                                     default_value=2.0, min_value=0.0, max_value=4.0, width=170, format="%.1f")

    dpg.add_spacer(height=4)
    dpg.add_text(
        "OFF = выкл  ·  ON = принудительно вкл  ·  "
        "RND = 50/50 каждый кат  ·  LCK = mix заморожен, "
        "RND-ALL и CLEAR не трогают",
        color=_GL_DIM, wrap=-1,
    )



def _build_palette_section():
    _section_sep("Цветовая гамма")
    palette_items = list(PALETTES.keys()) + ["custom"]
    with dpg.group(horizontal=True):
        dpg.add_combo(items=palette_items, default_value="auto",
                      tag="palette_combo", callback=_on_palette_change,
                      width=160)
        dpg.add_spacer(width=12)
        dpg.add_text("сила:", color=_DIM)
        dpg.add_slider_float(tag="palette_strength",
                             default_value=0.30, min_value=0.0, max_value=1.0,
                             width=200, format="%.2f")
    with dpg.group(horizontal=True, tag="pal_custom_row"):
        dpg.add_color_edit(label="Shadow", tag="pal_shadow",
                           default_value=[10, 30, 90, 255],
                           no_inputs=True, no_alpha=True, show=False)
        dpg.add_color_edit(label="Mid", tag="pal_mid",
                           default_value=[200, 90, 25, 255],
                           no_inputs=True, no_alpha=True, show=False)
        dpg.add_color_edit(label="Highlight", tag="pal_high",
                           default_value=[255, 220, 100, 255],
                           no_inputs=True, no_alpha=True, show=False)


def _build_timeline_section():
    with dpg.collapsing_header(label="  Временная шкала и рендер",
                               default_open=True):
        # 2-column parameter table for a cleaner layout
        with dpg.table(header_row=False, borders_innerV=False,
                       borders_outerV=False, borders_outerH=False,
                       borders_innerH=False, width=-1):
            dpg.add_table_column(width_fixed=True, init_width_or_weight=380)
            dpg.add_table_column(width_stretch=True)

            # row: duration / fps
            with dpg.table_row():
                with dpg.group():
                    dpg.add_slider_float(label="Длительность (с)", tag="duration",
                                         default_value=13.0, min_value=2.0, max_value=60.0,
                                         width=240, callback=_on_duration_change)
                with dpg.group():
                    dpg.add_slider_int(label="FPS", tag="fps",
                                       default_value=30, min_value=24, max_value=60,
                                       width=180)

            # row: offset / crf
            with dpg.table_row():
                with dpg.group():
                    dpg.add_slider_float(label="Старт аудио (с)", tag="offset",
                                         default_value=0.0, min_value=0.0, max_value=300.0,
                                         width=240, callback=_on_offset_change)
                    dpg.add_input_float(label="точно (с)", tag="offset_exact",
                                        default_value=0.0, step=0.5, width=100,
                                        callback=lambda _s, v: (
                                            dpg.set_value("offset", float(v)),
                                            _refresh_offset_label()))
                    dpg.add_text("00:00 → 00:13", tag="offset_label", color=_TEAL)
                with dpg.group():
                    dpg.add_slider_int(label="CRF", tag="crf",
                                       default_value=20, min_value=12, max_value=30,
                                       width=180)

            # row: aspect mode + crossfade
            with dpg.table_row():
                with dpg.group():
                    dpg.add_combo(
                        label="Формат",
                        items=[_ASPECT_LABELS[m] for m in _ASPECT_MODES_ORDER],
                        default_value=_ASPECT_LABELS["horizontal"],
                        tag="aspect_mode_combo",
                        callback=_on_aspect_mode_change,
                        width=200)
                    dpg.add_text(
                        "Хаос = размер меняется на каждом кате",
                        color=_DIM)
                with dpg.group():
                    dpg.add_slider_int(label="Crossfade ms", tag="crossfade_ms",
                                       default_value=300, min_value=0, max_value=1500,
                                       width=180)
                    dpg.add_combo(
                        label="Переход",
                        items=list(_TRANSITION_MODE_LABELS.keys()),
                        default_value="Random (микс)",
                        tag="transition_mode_combo",
                        width=200)
                    dpg.add_text(
                        "Эффект-переходы форсят шейдер на стыке клипов.\n"
                        "Pixel sort: РАЗМЕР разгоняется по дуге за время перехода.",
                        color=_DIM)

            # row: W/H (only used when aspect_mode == 'custom')
            with dpg.table_row():
                with dpg.group():
                    with dpg.group(horizontal=True):
                        dpg.add_slider_int(label="W", tag="width",
                                           default_value=1280, min_value=640, max_value=1920,
                                           width=160, enabled=False)
                        dpg.add_spacer(width=8)
                        dpg.add_slider_int(label="H", tag="height",
                                           default_value=720, min_value=360, max_value=1080,
                                           width=160, enabled=False)
                    dpg.add_text(
                        "W/H активны только при формате 'Custom'",
                        color=_DIM, tag="wh_hint")
                with dpg.group():
                    dpg.add_spacer(height=1)

            # row: cuts/beat (discrete combo) / flash
            with dpg.table_row():
                with dpg.group():
                    dpg.add_combo(
                        label="Cuts/beat",
                        items=list(_CUTS_PER_BEAT_LABELS.keys()),
                        default_value="×1 (на каждый бит)",
                        tag="cuts_per_beat_combo",
                        width=200)
                with dpg.group():
                    dpg.add_slider_int(label="Flash ms", tag="flash_ms",
                                       default_value=200, min_value=50, max_value=500,
                                       width=180)

            # row: beat remap (discrete combo) / cam stabilize
            with dpg.table_row():
                with dpg.group():
                    dpg.add_combo(
                        label="Beat remap",
                        items=list(_BEAT_REMAP_LABELS.keys()),
                        default_value="off",
                        tag="beat_remap_combo",
                        width=200)
                    dpg.add_text(
                        "off → extreme — степень пульсации скорости",
                        color=_DIM)
                with dpg.group():
                    dpg.add_checkbox(
                        label="Cam stabilize",
                        tag="cam_stabilize", default_value=False)
                    dpg.add_text(
                        "Убирает дрожание камеры",
                        color=_DIM)

            # row: random clip starts + preview scale
            with dpg.table_row():
                with dpg.group():
                    dpg.add_checkbox(
                        label="Случайные старты клипов",
                        tag="random_clip_starts", default_value=True)
                    dpg.add_text(
                        "Каждый кат — со случайной точки клипа",
                        color=_DIM)
                with dpg.group():
                    dpg.add_slider_float(
                        label="Preview масштаб",
                        tag="preview_scale",
                        default_value=0.5, min_value=0.15, max_value=1.0,
                        width=180, format="%.2f")
                    dpg.add_text(
                        "Меньше — плавнее превью. Render всегда полный.",
                        color=_DIM)

        dpg.add_spacer(height=6)

        # clip order section
        dpg.add_text("Порядок клипов:", color=_DIM)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=["bag", "shuffle", "random", "stride", "reverse"],
                default_value="bag",
                tag="clip_mode_combo",
                width=140)
            dpg.add_spacer(width=8)
            dpg.add_input_int(label="stride",
                              tag="clip_stride", default_value=2,
                              min_value=1, max_value=32, width=120)
            dpg.add_spacer(width=8)
            dpg.add_input_int(label="avoid recent",
                              tag="clip_avoid_recent", default_value=2,
                              min_value=0, max_value=16, width=140)
        dpg.add_text(
            "bag = каждый клип раз за круг (равномерно)  ·  "
            "shuffle = один порядок повторяется  ·  random = чистый рандом  ·  "
            "stride = через N клипов (чередование)  ·  reverse = с конца папки",
            color=_DIM, wrap=-1)
        dpg.add_text(
            "avoid recent = не повторять клип в течение N последних катов",
            color=_DIM, wrap=-1)

        dpg.add_spacer(height=6)

        # BPM override row
        dpg.add_text("BPM:", color=_DIM)
        with dpg.group(horizontal=True):
            dpg.add_input_float(
                tag="override_bpm",
                default_value=0.0,
                min_value=0.0, max_value=300.0,
                step=0.0, format="%.2f",
                width=160)
            dpg.add_text("0 = авто (HPSS + librosa)", color=_DIM)
        dpg.add_text(
            "Ставь вручную если автоматика поймала половинный/двойной темп.",
            color=_DIM, wrap=-1)

        dpg.add_spacer(height=4)

        # seed row
        with dpg.group(horizontal=True):
            dpg.add_text("Seed:", color=_DIM)
            dpg.add_input_int(tag="seed_input", default_value=0,
                              width=160, min_value=0, max_value=2_000_000_000)
            dpg.add_button(label="◆", width=30, height=24, callback=_new_seed_cb)
        dpg.add_text(
            "Seed 0 = новый при каждом Preview. Ненулевой = preview ≡ render.",
            color=_DIM, wrap=-1)


_VIBE_KEYS  = [""] + sorted(VIBE_TEXTS.keys())
_ALIGN_OPTS = ["center", "left", "right"]

# ── system font picker ──────────────────────────────────────────────────────
_FONT_DEFAULT_LABEL = "(встроенный)"
_FONT_DIRS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
]
_SYSTEM_FONTS: dict[str, str] = {}     # label → absolute path
_TC_PREVIEW_W = 340
_TC_PREVIEW_H = 80


def _enumerate_fonts() -> list[str]:
    """Scan system font dirs once; fill _SYSTEM_FONTS {stem: path}.
    Labels are filename stems (instant + reliable) — the live preview shows the
    actual typeface, so cryptic names don't matter."""
    global _SYSTEM_FONTS
    if not _SYSTEM_FONTS:
        found: dict[str, str] = {}
        for d in _FONT_DIRS:
            try:
                if not str(d) or not d.is_dir():
                    continue
                for f in sorted(d.iterdir()):
                    if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                        continue
                    label, base, i = f.stem, f.stem, 2
                    while label in found and found[label] != str(f):
                        label = f"{base} ({i})"; i += 1
                    found[label] = str(f)
            except Exception:
                pass
        _SYSTEM_FONTS = found
    return [_FONT_DEFAULT_LABEL] + sorted(_SYSTEM_FONTS, key=str.lower)


def _font_label_to_path(label: str) -> str:
    return _SYSTEM_FONTS.get(label, "")


def _font_path_to_label(path: str) -> str:
    if not path:
        return _FONT_DEFAULT_LABEL
    nc = os.path.normcase(str(path))
    for lbl, fp in _SYSTEM_FONTS.items():
        if os.path.normcase(fp) == nc:
            return lbl
    return Path(path).stem      # external/unscanned path → show its stem


def _refresh_tc_preview(*_):
    """Re-render the text-card preview swatch with the real renderer (WYSIWYG)."""
    if not dpg.does_item_exist("tc_preview_tex"):
        return
    def _g(t, d):
        return dpg.get_value(t) if dpg.does_item_exist(t) else d
    def _col(t, d):
        v = dpg.get_value(t) if dpg.does_item_exist(t) else None
        return (int(v[0]), int(v[1]), int(v[2])) if v else d

    font_path = _font_label_to_path(str(_g("tc_font", _FONT_DEFAULT_LABEL)))
    lines = [l.strip() for l in str(_g("tc_lines", "")).splitlines() if l.strip()]
    sample = lines[0] if lines else ""
    if not sample:
        vt = VIBE_TEXTS.get(str(_g("tc_vibe", "")), [])
        sample = vt[0] if vt else "Aa Bb 123 АаБб"
    try:
        import numpy as np
        from automatch import _render_text_card
        bgr = _render_text_card(
            sample, _TC_PREVIEW_W, _TC_PREVIEW_H,
            color=_col("tc_color", (255, 255, 255)),
            bg_color=_col("tc_bg_color", (0, 0, 0)),
            bg_alpha=1.0, font_scale=1.0,
            align=str(_g("tc_align", "center")), font_path=font_path)
        rgb  = np.ascontiguousarray(bgr[:, :, ::-1])
        a    = np.full((_TC_PREVIEW_H, _TC_PREVIEW_W, 1), 255, np.uint8)
        rgba = np.concatenate([rgb, a], axis=2).astype(np.float32) / 255.0
        dpg.set_value("tc_preview_tex", rgba.flatten().tolist())
    except Exception:
        pass


def _on_tc_vibe_change(_s, val):
    if dpg.does_item_exist("tc_vibe_preview"):
        texts  = VIBE_TEXTS.get(val, [])
        sample = " / ".join(texts[:4]) + (" / …" if len(texts) > 4 else "")
        dpg.set_value("tc_vibe_preview", sample or "—")
    _refresh_tc_preview()


def _set_tc_style(color, bg, bg_alpha):
    if dpg.does_item_exist("tc_color"):    dpg.set_value("tc_color",    [*color, 255])
    if dpg.does_item_exist("tc_bg_color"): dpg.set_value("tc_bg_color", [*bg, 255])
    if dpg.does_item_exist("tc_bg_alpha"): dpg.set_value("tc_bg_alpha", bg_alpha)
    _refresh_tc_preview()


def _build_text_cards_section():
    with dpg.collapsing_header(label="  Текстовые карточки",
                               default_open=False):
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Включить", tag="tc_enabled", default_value=False)
            dpg.add_spacer(width=16)
            dpg.add_slider_float(label="Вероятность", tag="tc_prob",
                                 default_value=0.40, min_value=0.0, max_value=1.0,
                                 width=160, format="%.2f")
            dpg.add_spacer(width=12)
            dpg.add_slider_float(label="Длина (с)", tag="tc_dur",
                                 default_value=0.80, min_value=0.1, max_value=3.0,
                                 width=140, format="%.2f")
        dpg.add_spacer(height=4)

        with dpg.group(horizontal=True):
            dpg.add_combo(items=_VIBE_KEYS, default_value="", tag="tc_vibe",
                          width=160, callback=_on_tc_vibe_change, label="вайб")
            dpg.add_spacer(width=8)
            dpg.add_text("—", tag="tc_vibe_preview", color=_TEAL)

        dpg.add_input_text(tag="tc_lines", default_value="",
                           hint="FRAGMENT\n3:47 AM\nNEXT LEVEL",
                           multiline=True, height=65, width=-1,
                           callback=_refresh_tc_preview)
        dpg.add_spacer(height=4)

        with dpg.group(horizontal=True):
            dpg.add_color_edit(label="Текст",  tag="tc_color",
                               default_value=[255,255,255,255],
                               no_inputs=True, no_alpha=True,
                               callback=_refresh_tc_preview)
            dpg.add_spacer(width=8)
            dpg.add_color_edit(label="Фон",    tag="tc_bg_color",
                               default_value=[0,0,0,255],
                               no_inputs=True, no_alpha=True,
                               callback=_refresh_tc_preview)
            dpg.add_spacer(width=8)
            dpg.add_slider_float(label="Фон α", tag="tc_bg_alpha",
                                 default_value=1.0, min_value=0.0, max_value=1.0,
                                 width=120, format="%.2f")
            dpg.add_spacer(width=8)
            dpg.add_slider_float(label="Шрифт ×", tag="tc_font_scale",
                                 default_value=1.0, min_value=0.3, max_value=3.0,
                                 width=100, format="%.1f")
            dpg.add_spacer(width=8)
            dpg.add_combo(items=_ALIGN_OPTS, default_value="center",
                          tag="tc_align", width=100,
                          callback=_refresh_tc_preview)
        dpg.add_spacer(height=4)

        # ── font picker + live preview ──
        with dpg.group(horizontal=True):
            dpg.add_combo(items=_enumerate_fonts(),
                          default_value=_FONT_DEFAULT_LABEL,
                          tag="tc_font", width=240,
                          callback=_refresh_tc_preview, label="шрифт")
            dpg.add_spacer(width=8)
            dpg.add_text("превью \\/", color=_DIM)
        dpg.add_image("tc_preview_tex",
                      width=_TC_PREVIEW_W, height=_TC_PREVIEW_H)
        dpg.add_spacer(height=4)

        with dpg.group(horizontal=True):
            dpg.add_button(label="Solid black", width=100,
                callback=lambda: _set_tc_style((255,255,255),(0,0,0),1.0))
            dpg.add_button(label="Overlay",     width=90,
                callback=lambda: _set_tc_style((255,255,255),(0,0,0),0.0))
            dpg.add_button(label="GitS green",  width=100,
                callback=lambda: _set_tc_style((80,255,140),(0,8,12),1.0))
            dpg.add_button(label="Amber",       width=80,
                callback=lambda: _set_tc_style((255,180,40),(10,6,0),1.0))
        _refresh_tc_preview()


_BLOB_TRACKER_MODES = (
    "hsv · cвежий каждый кадр (текущий)",
    "csrt · OpenCV CSRT (точный, ~25 fps)",
    "kcf · OpenCV KCF (быстрый, ~150 fps)",
    "mosse · OpenCV MOSSE (макс. скорость, ~600 fps)",
)
_BLOB_TRACKER_KEYS = ("hsv", "csrt", "kcf", "mosse")

# Bracket marker styles (index → BlobTrackSettings.bracket_style)
_BLOB_BRACKET_STYLES = (
    "Углы (скобки)", "Прямоугольник", "Круг-ретикль", "Пунктир",
)
# HUD timestamp formats (index → BlobTrackSettings.timestamp_format)
_BLOB_TS_FORMATS = (
    "Дата + время", "Только время", "Только дата", "Счётчик кадров",
)


def _build_blob_section():
    with dpg.collapsing_header(label="  Blob Tracking",
                               default_open=False):
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Включить blob tracking",
                             tag="bt_enabled", default_value=False)
            dpg.add_spacer(width=16)
            dpg.add_checkbox(label="Zoom to blob", tag="bt_zoom",
                             default_value=False)
        dpg.add_spacer(height=4)
        dpg.add_combo(items=list(_BLOB_TRACKER_MODES),
                      default_value=_BLOB_TRACKER_MODES[0],
                      tag="bt_tracker_mode_combo",
                      label="Tracker",
                      width=320)
        dpg.add_text(
            "HSV: ловит цвет по порогам, ID каждый кадр новый.  "
            "CSRT/KCF/MOSSE: stable IDs, инициализируются с HSV и держат "
            "цель между кадрами; при потере — переинициализируются.",
            color=_GL_DIM, wrap=-1,
        )
        dpg.add_spacer(height=6)

        with dpg.group(horizontal=True):
            dpg.add_slider_int(label="Оттенок (Hue)", tag="bt_hue",
                               default_value=120, min_value=0, max_value=179, width=180)
            dpg.add_spacer(width=12)
            dpg.add_slider_int(label="±", tag="bt_hue_range",
                               default_value=30, min_value=5, max_value=89, width=120)
        with dpg.group(horizontal=True):
            for name, val in _HUE_PRESETS.items():
                dpg.add_button(label=name, width=72,
                    callback=lambda _s, _a, v=val: dpg.set_value("bt_hue", v))
        dpg.add_spacer(height=4)

        with dpg.group(horizontal=True):
            dpg.add_slider_int(label="Мин яркость",     tag="bt_min_val",  default_value=80,  min_value=0,   max_value=255,   width=150)
            dpg.add_spacer(width=8)
            dpg.add_slider_int(label="Мин насыщ",       tag="bt_min_sat",  default_value=50,  min_value=0,   max_value=255,   width=150)
            dpg.add_spacer(width=8)
            dpg.add_slider_int(label="Мин размер px²",  tag="bt_min_size", default_value=800, min_value=100, max_value=20000, width=150)
            dpg.add_spacer(width=8)
            dpg.add_slider_int(label="Макс блобов",     tag="bt_max_blobs",default_value=3,   min_value=1,   max_value=8,     width=110)
        dpg.add_spacer(height=4)

        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Скобки",  tag="bt_overlay",  default_value=True)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Метки",   tag="bt_labels",   default_value=True)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Центроид",tag="bt_centroid", default_value=True)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Data line",tag="bt_dataline",default_value=True)
        with dpg.group(horizontal=True):
            dpg.add_color_edit(label="Цвет скобок", tag="bt_col",
                               default_value=[80,255,140,255],
                               no_inputs=True, no_alpha=True)
            dpg.add_spacer(width=10)
            dpg.add_slider_int(label="Толщина", tag="bt_thickness",
                               default_value=2, min_value=1, max_value=5, width=120)
        with dpg.group(horizontal=True):
            dpg.add_button(label="GitS green", width=100,
                callback=lambda: dpg.set_value("bt_col", [80,255,140,255]))
            dpg.add_button(label="Cyan",  width=80,
                callback=lambda: dpg.set_value("bt_col", [60,220,255,255]))
            dpg.add_button(label="Amber", width=80,
                callback=lambda: dpg.set_value("bt_col", [255,180,40,255]))
            dpg.add_button(label="White", width=80,
                callback=lambda: dpg.set_value("bt_col", [255,255,255,255]))

        # ── visual extras: lines / markers / labels / HUD ──────────────
        dpg.add_spacer(height=8)
        dpg.add_text("ЛИНИИ И МАРКЕРЫ", color=_GL_DIM)
        dpg.add_combo(items=list(_BLOB_BRACKET_STYLES),
                      default_value=_BLOB_BRACKET_STYLES[0],
                      tag="bt_bracket_style_combo",
                      label="Стиль рамки", width=320)
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Полноэкранный прицел", tag="bt_crosshair",
                             default_value=False)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Линия-выноска", tag="bt_leader",
                             default_value=False)
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Трейлы движения", tag="bt_trails",
                             default_value=False)
            dpg.add_spacer(width=8)
            dpg.add_slider_int(label="Длина трейла", tag="bt_trail_len",
                               default_value=12, min_value=2, max_value=60,
                               width=160)
        dpg.add_checkbox(label="Сеть-связи между блобами", tag="bt_network",
                         default_value=False)

        dpg.add_spacer(height=8)
        dpg.add_text("МЕТКИ И ДАТА", color=_GL_DIM)
        with dpg.group(horizontal=True):
            dpg.add_input_text(label="Префикс метки", tag="bt_label_prefix",
                               default_value="TGT", width=160)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Доп. данные в строке", tag="bt_extra_data",
                             default_value=False)
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="HUD-таймстамп", tag="bt_timestamp",
                             default_value=False)
            dpg.add_spacer(width=8)
            dpg.add_combo(items=list(_BLOB_TS_FORMATS),
                          default_value=_BLOB_TS_FORMATS[0],
                          tag="bt_ts_format_combo",
                          label="Формат", width=200)


# ═══════════════════════════════════════════════════════════════════════════════
# ModMatrix GUI (Slice 2)
# ═══════════════════════════════════════════════════════════════════════════════

_MOD_N_ROUTES = 8

# Curated target param list shown in route combos
_MOD_PARAM_TARGETS = (
    # effect mixes (generated at build time via USER_EFFECTS)
    *[f"effect_mix_{k}" for k in USER_EFFECTS],
    # common state params
    "vhs_noise", "vhs_tracking", "vhs_color_bleed",
    "wave_warp_strength", "wave_warp_speed", "wave_warp_scale",
    "datamosh_strength", "datamosh_chaos",
    "pixel_sort_threshold", "pixel_sort_length",
    "distort_scale", "distort_speed",
    "rgb_split_offset", "rgb_split_drift",
    "ascii_cell_size",
    "dither_levels", "dither_jitter",
    "crt_deflection", "crt_convergence",
    "heavy_grain_amount", "heavy_grain_size",
    "motion_trails_decay",
    "kaleidoscope_segments", "kaleidoscope_spin",
    "slit_speed", "slit_feedback",
    "thermal_palette",
    "cellular_speed",
    "elastic_grid_strength",
)

_MOD_LFO_WAVE_LABELS = {
    "sine": "Sine", "triangle": "Tri", "saw": "Saw",
    "square": "Square", "random": "Rand", "sh": "S&H",
}
_MOD_SOURCE_LABELS = {
    "off": "off", "energy": "energy", "rms": "rms",
    "beat": "beat", "downbeat": "downbeat", "drop": "drop",
    "lfo1": "LFO1", "lfo2": "LFO2", "lfo3": "LFO3", "lfo4": "LFO4",
}
_MOD_COMBINE_LABELS = {"add": "add", "mul": "mul", "avg": "avg"}
_MOD_MODE_LABELS    = {"bipolar": "bipolar", "absolute": "absolute"}


def _collect_mod_config() -> dict:
    """Read ModMatrix widgets → mod_config dict (JSON-serialisable)."""
    if not dpg.does_item_exist("mod_enabled") or not dpg.get_value("mod_enabled"):
        return {}

    lfos = []
    for i, name in enumerate(LFO_NAMES, 1):
        wave_lbl = dpg.get_value(f"mod_lfo{i}_wave") if dpg.does_item_exist(f"mod_lfo{i}_wave") else "Sine"
        wave = next((k for k, v in _MOD_LFO_WAVE_LABELS.items() if v == wave_lbl), "sine")
        lfos.append({
            "name": name,
            "wave": wave,
            "rate":               float(dpg.get_value(f"mod_lfo{i}_rate"))   if dpg.does_item_exist(f"mod_lfo{i}_rate")   else 1.0,
            "beat_sync":          bool(dpg.get_value(f"mod_lfo{i}_sync"))    if dpg.does_item_exist(f"mod_lfo{i}_sync")    else False,
            "phase":              float(dpg.get_value(f"mod_lfo{i}_phase"))  if dpg.does_item_exist(f"mod_lfo{i}_phase")  else 0.0,
            "reset_on_downbeat":  bool(dpg.get_value(f"mod_lfo{i}_rdb"))    if dpg.does_item_exist(f"mod_lfo{i}_rdb")    else False,
        })

    routes = []
    for i in range(1, _MOD_N_ROUTES + 1):
        src_lbl = dpg.get_value(f"mod_r{i}_src") if dpg.does_item_exist(f"mod_r{i}_src") else "off"
        src = next((k for k, v in _MOD_SOURCE_LABELS.items() if v == src_lbl), "off")
        if src == "off":
            continue
        tgt = dpg.get_value(f"mod_r{i}_tgt") if dpg.does_item_exist(f"mod_r{i}_tgt") else ""
        if not tgt:
            continue
        mode_lbl = dpg.get_value(f"mod_r{i}_mode") if dpg.does_item_exist(f"mod_r{i}_mode") else "bipolar"
        mode = next((k for k, v in _MOD_MODE_LABELS.items() if v == mode_lbl), "bipolar")
        comb_lbl = dpg.get_value(f"mod_r{i}_comb") if dpg.does_item_exist(f"mod_r{i}_comb") else "add"
        combine = next((k for k, v in _MOD_COMBINE_LABELS.items() if v == comb_lbl), "add")
        routes.append({
            "target":  tgt,
            "source":  src,
            "mode":    mode,
            "depth":   float(dpg.get_value(f"mod_r{i}_depth")) if dpg.does_item_exist(f"mod_r{i}_depth") else 0.2,
            "curve":   float(dpg.get_value(f"mod_r{i}_curve")) if dpg.does_item_exist(f"mod_r{i}_curve") else 1.0,
            "lo":      float(dpg.get_value(f"mod_r{i}_lo"))    if dpg.does_item_exist(f"mod_r{i}_lo")    else 0.0,
            "hi":      float(dpg.get_value(f"mod_r{i}_hi"))    if dpg.does_item_exist(f"mod_r{i}_hi")    else 1.0,
            "invert":  bool(dpg.get_value(f"mod_r{i}_inv"))    if dpg.does_item_exist(f"mod_r{i}_inv")   else False,
            "combine": combine,
        })

    return {"lfos": lfos, "routes": routes}


def _apply_mod_config(d: dict) -> None:
    """Push saved mod_config dict back into ModMatrix widgets."""
    if not d:
        return
    def _s(tag, val):
        if dpg.does_item_exist(tag):
            try: dpg.set_value(tag, val)
            except Exception: pass

    _s("mod_enabled", True)

    for i, ld in enumerate(d.get("lfos", [])[:4], 1):
        wave_lbl = _MOD_LFO_WAVE_LABELS.get(str(ld.get("wave", "sine")), "Sine")
        _s(f"mod_lfo{i}_wave",  wave_lbl)
        _s(f"mod_lfo{i}_rate",  float(ld.get("rate", 1.0)))
        _s(f"mod_lfo{i}_sync",  bool(ld.get("beat_sync", False)))
        _s(f"mod_lfo{i}_phase", float(ld.get("phase", 0.0)))
        _s(f"mod_lfo{i}_rdb",   bool(ld.get("reset_on_downbeat", False)))

    for i, rd in enumerate(d.get("routes", [])[:_MOD_N_ROUTES], 1):
        src_lbl  = _MOD_SOURCE_LABELS.get(str(rd.get("source", "off")), "off")
        mode_lbl = _MOD_MODE_LABELS.get(str(rd.get("mode", "bipolar")), "bipolar")
        comb_lbl = _MOD_COMBINE_LABELS.get(str(rd.get("combine", "add")), "add")
        _s(f"mod_r{i}_tgt",   str(rd.get("target", "")))
        _s(f"mod_r{i}_src",   src_lbl)
        _s(f"mod_r{i}_mode",  mode_lbl)
        _s(f"mod_r{i}_depth", float(rd.get("depth", 0.2)))
        _s(f"mod_r{i}_curve", float(rd.get("curve", 1.0)))
        _s(f"mod_r{i}_lo",    float(rd.get("lo", 0.0)))
        _s(f"mod_r{i}_hi",    float(rd.get("hi", 1.0)))
        _s(f"mod_r{i}_inv",   bool(rd.get("invert", False)))
        _s(f"mod_r{i}_comb",  comb_lbl)


def _clear_mod_config() -> None:
    """Reset all ModMatrix widgets to their defaults (no routes active)."""
    def _s(tag, val):
        if dpg.does_item_exist(tag):
            try: dpg.set_value(tag, val)
            except Exception: pass
    _s("mod_enabled", False)
    for i in range(1, 5):
        _s(f"mod_lfo{i}_wave",  "Sine")
        _s(f"mod_lfo{i}_rate",  1.0)
        _s(f"mod_lfo{i}_sync",  False)
        _s(f"mod_lfo{i}_phase", 0.0)
        _s(f"mod_lfo{i}_rdb",   False)
    for i in range(1, _MOD_N_ROUTES + 1):
        _s(f"mod_r{i}_tgt",   "")
        _s(f"mod_r{i}_src",   "off")
        _s(f"mod_r{i}_mode",  "bipolar")
        _s(f"mod_r{i}_depth", 0.2)
        _s(f"mod_r{i}_curve", 1.0)
        _s(f"mod_r{i}_lo",    0.0)
        _s(f"mod_r{i}_hi",    1.0)
        _s(f"mod_r{i}_inv",   False)
        _s(f"mod_r{i}_comb",  "add")


# ═══════════════════════════════════════════════════════════════════════════════
# Effect Chain — user-defined render order
# ═══════════════════════════════════════════════════════════════════════════════

_CHAIN_ORDER: list = list(USER_EFFECTS)   # mutable, persists in session


def _collect_effect_chain() -> list:
    if not dpg.does_item_exist("chain_enabled") or not dpg.get_value("chain_enabled"):
        return []
    return list(_CHAIN_ORDER)


def _apply_effect_chain_gui(chain: list) -> None:
    global _CHAIN_ORDER
    if not chain:
        _CHAIN_ORDER = list(USER_EFFECTS)
    else:
        seen = set(chain)
        _CHAIN_ORDER = [k for k in chain if k in set(USER_EFFECTS)]
        for k in USER_EFFECTS:
            if k not in seen:
                _CHAIN_ORDER.append(k)


_CHAIN_SWAP_SOURCE: str = ""   # first-click selection for two-step swap


def _chain_reset():
    global _CHAIN_ORDER
    _CHAIN_ORDER = list(USER_EFFECTS)
    _rebuild_matrix()


def _chain_rebind_btn(kind: str, selected: bool):
    """Rebind only a single matrix button's theme — no rebuild needed."""
    tag = f"fx_btn_{kind}"
    if not dpg.does_item_exist(tag) or not _BTN_THEMES:
        return
    state = _FX_STATE.get(kind, "off")
    if selected and _BTN_THEMES_SEL:
        theme = _BTN_THEMES_SEL.get("on")   # bright green border = selected
    else:
        theme = _BTN_THEMES.get(state)
    if theme:
        dpg.bind_item_theme(tag, theme)


def _chain_cell_click(kind: str):
    """Two-step swap: first click selects (theme-only), second click swaps + rebuilds."""
    global _CHAIN_SWAP_SOURCE
    if not dpg.does_item_exist("chain_enabled") or not dpg.get_value("chain_enabled"):
        return
    if _CHAIN_SWAP_SOURCE == "":
        # Step 1 — just highlight, NO rebuild (safe from within button callback)
        _CHAIN_SWAP_SOURCE = kind
        _chain_rebind_btn(kind, selected=True)
        if dpg.does_item_exist("status"):
            dpg.set_value("status",
                f"Цепочка: [{_FX_ABBR.get(kind, kind)}] выбран — кликни другую ячейку")
    elif _CHAIN_SWAP_SOURCE == kind:
        # Deselect — just restore theme, NO rebuild
        prev = _CHAIN_SWAP_SOURCE
        _CHAIN_SWAP_SOURCE = ""
        _chain_rebind_btn(prev, selected=False)
        if dpg.does_item_exist("status"):
            dpg.set_value("status", "Цепочка: отменено")
    else:
        # Step 2 — swap + rebuild (safe: we are in a DIFFERENT button's callback)
        src, dst = _CHAIN_SWAP_SOURCE, kind
        _CHAIN_SWAP_SOURCE = ""
        try:
            si = _CHAIN_ORDER.index(src)
            di = _CHAIN_ORDER.index(dst)
        except ValueError:
            return
        _CHAIN_ORDER[si], _CHAIN_ORDER[di] = _CHAIN_ORDER[di], _CHAIN_ORDER[si]
        _rebuild_matrix()
        if dpg.does_item_exist("status"):
            a, b = _FX_ABBR.get(src, src), _FX_ABBR.get(dst, dst)
            dpg.set_value("status", f"Цепочка: [{a}] ↔ [{b}]")


def _rebuild_matrix():
    """Rebuild matrix cells in current _CHAIN_ORDER order.
    Preserves mix/randmix values and rebinds state themes."""
    if not dpg.does_item_exist("fx_matrix_container"):
        return

    # Snapshot slider/checkbox values before deletion
    saved_mix = {k: dpg.get_value(f"fx_mix_{k}")
                 for k in USER_EFFECTS if dpg.does_item_exist(f"fx_mix_{k}")}
    saved_rnd = {k: dpg.get_value(f"fx_randmix_{k}")
                 for k in USER_EFFECTS if dpg.does_item_exist(f"fx_randmix_{k}")}

    dpg.delete_item("fx_matrix_container", children_only=True)

    cols, cell_w, cell_h = 4, 84, 46
    cells = list(_CHAIN_ORDER)

    for r in range(0, len(cells), cols):
        row_cells = cells[r:r + cols]
        with dpg.group(horizontal=True, parent="fx_matrix_container"):
            for kind in row_cells:
                chain_on = dpg.does_item_exist("chain_enabled") and dpg.get_value("chain_enabled")
                is_swap_src = (kind == _CHAIN_SWAP_SOURCE)
                with dpg.group():
                    dpg.add_button(
                        label=_FX_ABBR.get(kind, kind[:5].upper()),
                        tag=f"fx_btn_{kind}",
                        width=cell_w, height=cell_h,
                        callback=(lambda s, a, k=kind: _chain_cell_click(k))
                                  if chain_on else _cycle_fx_state,
                        user_data=kind,
                    )
                    dpg.add_text(_FX_SHORT.get(kind, kind),
                                 color=_GL_DIM, wrap=cell_w)
                # Hidden sliders — siblings of the cell group
                dpg.add_slider_float(
                    tag=f"fx_mix_{kind}",
                    default_value=saved_mix.get(kind, 0.65),
                    min_value=0.0, max_value=1.0,
                    show=False,
                )
                dpg.add_checkbox(
                    tag=f"fx_randmix_{kind}",
                    default_value=saved_rnd.get(kind, False),
                    show=False,
                )

    # Rebind button + slider themes to match FX state
    for kind in USER_EFFECTS:
        btn = f"fx_btn_{kind}"
        if not dpg.does_item_exist(btn) or not _BTN_THEMES:
            continue
        state = _FX_STATE.get(kind, "off")
        sel   = (kind == _SELECTED_FX)
        # swap-source gets the selected theme regardless of effect state
        if kind == _CHAIN_SWAP_SOURCE and _BTN_THEMES_SEL:
            theme = _BTN_THEMES_SEL.get("on")   # bright green border = "selected to swap"
        else:
            theme = (_BTN_THEMES_SEL if sel else _BTN_THEMES).get(state)
        if theme:
            dpg.bind_item_theme(btn, theme)
        mix = f"fx_mix_{kind}"
        if dpg.does_item_exist(mix) and _SLIDER_ACTIVE and _SLIDER_DIMMED:
            dpg.bind_item_theme(
                mix,
                _SLIDER_ACTIVE if state in ("on", "random", "locked") else _SLIDER_DIMMED,
            )


def _build_chain_tiles():
    """Drag-drop tile grid — call from inside _build_effects_section()."""
    global _CHAIN_TILES_BUILT
    if _CHAIN_TILES_BUILT:
        return
    _CHAIN_TILES_BUILT = True

    dpg.add_spacer(height=8)
    dpg.add_separator()
    dpg.add_spacer(height=4)

    with dpg.group(horizontal=True):
        dpg.add_checkbox(label="Активировать цепочку",
                         tag="chain_enabled", default_value=False)
        dpg.add_spacer(width=12)
        dpg.add_button(label="СБРОС", width=70,
                       callback=lambda: _chain_reset())
        dpg.add_spacer(width=8)
        dpg.add_text("← Тащи тайлы чтобы изменить порядок рендера",
                     color=_GL_DIM)
    dpg.add_spacer(height=6)

    n = len(_CHAIN_ORDER)
    for row_start in range(0, n, _TILES_PER_ROW):
        with dpg.group(horizontal=True):
            for slot in range(row_start, min(row_start + _TILES_PER_ROW, n)):
                kind  = _CHAIN_ORDER[slot]
                state = _FX_STATE.get(kind, "off")
                abbr  = _FX_ABBR.get(kind, kind[:5].upper())
                tag   = f"chain_tile_{slot}"

                dpg.add_button(
                    label=f"{slot+1:02d}\n{abbr}",
                    tag=tag,
                    width=_TILE_W, height=_TILE_H,
                    drop_callback=_make_chain_drop_cb(slot),
                )
                if _BTN_THEMES:
                    theme = _BTN_THEMES.get(state)
                    if theme:
                        dpg.bind_item_theme(tag, theme)

                with dpg.drag_payload(parent=tag, drag_data=slot,
                                      payload_type="CHAIN_TILE"):
                    dpg.add_text(f"{slot+1}·{abbr}")

                dpg.add_spacer(width=3)
        dpg.add_spacer(height=3)


def _build_mod_matrix_section():
    _wave_labels  = list(_MOD_LFO_WAVE_LABELS.values())
    _src_labels   = list(_MOD_SOURCE_LABELS.values())
    _mode_labels  = list(_MOD_MODE_LABELS.values())
    _comb_labels  = list(_MOD_COMBINE_LABELS.values())
    _tgt_labels   = list(_MOD_PARAM_TARGETS)

    with dpg.collapsing_header(label="  Mod Matrix", default_open=False):
        dpg.add_checkbox(label="Включить модуляцию", tag="mod_enabled",
                         default_value=False)
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="BEAT SPIKE",
                width=120,
                callback=lambda: (
                    _apply_mod_config(BEAT_SPIKE_PRESET),
                    dpg.set_value("mod_enabled", True),
                ),
            )
            dpg.add_spacer(width=6)
            dpg.add_button(
                label="DOWNBEAT PULSE",
                width=150,
                callback=lambda: (
                    _apply_mod_config(DOWNBEAT_PULSE_PRESET),
                    dpg.set_value("mod_enabled", True),
                ),
            )
            dpg.add_spacer(width=6)
            dpg.add_button(
                label="CLEAR",
                width=80,
                callback=lambda: _clear_mod_config(),
            )
        dpg.add_spacer(height=6)

        # ── LFOs ──────────────────────────────────────────────────────────────
        dpg.add_text("LFO", color=_GL_MAGENTA)
        dpg.add_separator()
        for i in range(1, 5):
            with dpg.group(horizontal=True):
                dpg.add_text(f"LFO{i}", color=_GL_DIM)
                dpg.add_spacer(width=4)
                dpg.add_combo(items=_wave_labels,
                              default_value="Sine",
                              tag=f"mod_lfo{i}_wave",
                              width=72, no_preview=False)
                dpg.add_spacer(width=4)
                dpg.add_slider_float(tag=f"mod_lfo{i}_rate",
                                     default_value=1.0, min_value=0.01, max_value=32.0,
                                     width=120, format="%.2f Hz")
                dpg.add_spacer(width=4)
                dpg.add_checkbox(label="Beat", tag=f"mod_lfo{i}_sync", default_value=False)
                dpg.add_spacer(width=8)
                dpg.add_slider_float(tag=f"mod_lfo{i}_phase",
                                     default_value=0.0, min_value=0.0, max_value=1.0,
                                     width=90, format="ph %.2f")
                dpg.add_spacer(width=4)
                dpg.add_checkbox(label="↓Beat", tag=f"mod_lfo{i}_rdb", default_value=False)
            dpg.add_spacer(height=2)

        dpg.add_spacer(height=6)

        # ── Routes ────────────────────────────────────────────────────────────
        dpg.add_text("ROUTES", color=_GL_CYAN)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_text("   Target", color=_GL_DIM)
            dpg.add_spacer(width=148)
            dpg.add_text("Source", color=_GL_DIM)
            dpg.add_spacer(width=58)
            dpg.add_text("Mode", color=_GL_DIM)
            dpg.add_spacer(width=62)
            dpg.add_text("Depth", color=_GL_DIM)
            dpg.add_spacer(width=72)
            dpg.add_text("Curve", color=_GL_DIM)
            dpg.add_spacer(width=64)
            dpg.add_text("Lo", color=_GL_DIM)
            dpg.add_spacer(width=68)
            dpg.add_text("Hi", color=_GL_DIM)
        dpg.add_spacer(height=2)

        for i in range(1, _MOD_N_ROUTES + 1):
            with dpg.group(horizontal=True):
                dpg.add_text(f"R{i}", color=_GL_DIM)
                dpg.add_spacer(width=4)
                dpg.add_combo(items=_tgt_labels,
                              default_value="",
                              tag=f"mod_r{i}_tgt",
                              width=190)
                dpg.add_spacer(width=4)
                dpg.add_combo(items=_src_labels,
                              default_value="off",
                              tag=f"mod_r{i}_src",
                              width=88)
                dpg.add_spacer(width=4)
                dpg.add_combo(items=_mode_labels,
                              default_value="bipolar",
                              tag=f"mod_r{i}_mode",
                              width=88)
                dpg.add_spacer(width=4)
                dpg.add_slider_float(tag=f"mod_r{i}_depth",
                                     default_value=0.2, min_value=0.0, max_value=1.0,
                                     width=90, format="%.2f")
                dpg.add_spacer(width=4)
                dpg.add_slider_float(tag=f"mod_r{i}_curve",
                                     default_value=1.0, min_value=0.1, max_value=4.0,
                                     width=80, format="^%.1f")
                dpg.add_spacer(width=4)
                dpg.add_slider_float(tag=f"mod_r{i}_lo",
                                     default_value=0.0, min_value=0.0, max_value=1.0,
                                     width=80, format="%.2f")
                dpg.add_spacer(width=4)
                dpg.add_slider_float(tag=f"mod_r{i}_hi",
                                     default_value=1.0, min_value=0.0, max_value=1.0,
                                     width=80, format="%.2f")
                dpg.add_spacer(width=4)
                dpg.add_checkbox(label="inv", tag=f"mod_r{i}_inv", default_value=False)
                dpg.add_spacer(width=4)
                dpg.add_combo(items=_comb_labels,
                              default_value="add",
                              tag=f"mod_r{i}_comb",
                              width=60)
            dpg.add_spacer(height=1)


# ── themed strip helpers (GL1TCH header + footer) ───────────────────────────
_HEADER_THEME: int = 0
_FOOTER_THEME: int = 0

# ── procedural textures (glitch strips + Bayer dither) ──────────────────────
_TEX_GLITCH_TOP:    int | str = 0
_TEX_GLITCH_BOT:    int | str = 0
_TEX_DITHER_SEP:    int | str = 0
_TEX_W = 1200          # nominal width; image widget stretches to container


def _make_glitch_strip(width: int, height: int, seed: int) -> list[float]:
    """Datamosh-style horizontal corruption band.

    Two layers stacked:
      • a base of 4-8 px RGB blocks pulled from the GL1TCH accent palette
        (cyan / magenta / green / yellow / off-black), each block randomly
        offset and tinted — feels like P-frame residual error;
      • horizontal "scanline shear" rows (every ~6 px) that pick a single
        bright colour and stretch it across, mimicking a corrupted slice
        header in a DV25 stream.
    Returned as a flat list[float] in RGBA 0..1 ready for add_static_texture.
    """
    import numpy as np
    rng = np.random.default_rng(seed)

    palette = np.array([
        _GL_MAGENTA, _GL_CYAN, _GL_GREEN,
        (255, 235,  60),         # warning yellow
        (255,  80,  80),         # red glitch
        (12,   8,  14),          # near-black gap
        (255, 255, 255),         # blown highlight
    ], dtype=np.uint8)

    img = np.zeros((height, width, 4), dtype=np.uint8)
    img[..., 3] = 255            # fully opaque

    # 1) random block fill
    block_w = rng.integers(3, 9, size=width // 3 + 1)
    x = 0
    while x < width:
        bw = int(block_w[min(x // 3, len(block_w) - 1)])
        bw = min(bw, width - x)
        col = palette[rng.integers(0, len(palette))]
        # vertical wobble: not all rows the same colour
        y_off = int(rng.integers(0, max(1, height // 2)))
        img[y_off:, x:x + bw, :3] = col
        # fill the top rows with a darker tint
        dark = (col.astype(np.int16) * rng.uniform(0.2, 0.6)).astype(np.uint8)
        img[:y_off, x:x + bw, :3] = dark
        x += bw

    # 2) scanline shears — pick rows and overwrite with a single bright tone
    shear_count = max(1, height // 4)
    for _ in range(shear_count):
        y = int(rng.integers(0, height))
        col = palette[rng.integers(0, 3)]            # only the 3 neons
        img[y:y + 1, :, :3] = col

    # 3) sparse "drop-out" black pixels
    dropouts = rng.random((height, width)) < 0.04
    img[dropouts, :3] = 0

    # convert uint8 RGBA → flat list[float] in [0,1]
    return (img.astype(np.float32) / 255.0).flatten().tolist()


def _make_dither_strip(width: int, height: int) -> list[float]:
    """A thin Bayer-style dither separator — 4×4 ordered-dither pattern in
    magenta on near-black, with subtle per-channel shifts so it reads as
    'analog dispersion' rather than a flat texture.  Used in place of plain
    `dpg.add_separator()` inside `_section_sep()`.
    """
    import numpy as np

    bayer4 = np.array([
        [ 0,  8,  2, 10],
        [12,  4, 14,  6],
        [ 3, 11,  1,  9],
        [15,  7, 13,  5],
    ], dtype=np.float32) / 16.0

    # tile to fill the strip
    tile = np.tile(bayer4, (max(1, height // 4 + 1), max(1, width // 4 + 1)))
    tile = tile[:height, :width]

    # threshold at 0.5 → on-pixels get magenta, off-pixels stay near-black
    fg = np.array(_GL_MAGENTA, dtype=np.float32) / 255.0
    bg = np.array((10, 6, 14),  dtype=np.float32) / 255.0

    rgba = np.zeros((height, width, 4), dtype=np.float32)
    mask = (tile > 0.45)                         # ~half the pixels lit
    rgba[..., 0] = np.where(mask, fg[0], bg[0])
    rgba[..., 1] = np.where(mask, fg[1], bg[1])
    rgba[..., 2] = np.where(mask, fg[2], bg[2])
    # tiny cyan kicker on every 7th column for chromatic shift
    rgba[:, ::7, 1] = np.maximum(rgba[:, ::7, 1], 0.55)   # G boost
    rgba[:, ::7, 2] = np.maximum(rgba[:, ::7, 2], 0.55)   # B boost
    rgba[..., 3] = 1.0
    return rgba.flatten().tolist()


def _register_glitch_textures():
    """Generate + register all decorative textures.
    Call after dpg.setup_dearpygui() (needs a live texture registry)."""
    global _TEX_GLITCH_TOP, _TEX_GLITCH_BOT, _TEX_DITHER_SEP
    with dpg.texture_registry(show=False):
        _TEX_GLITCH_TOP = dpg.add_static_texture(
            width=_TEX_W, height=10,
            default_value=_make_glitch_strip(_TEX_W, 10, seed=7))
        _TEX_GLITCH_BOT = dpg.add_static_texture(
            width=_TEX_W, height=10,
            default_value=_make_glitch_strip(_TEX_W, 10, seed=42))
        _TEX_DITHER_SEP = dpg.add_static_texture(
            width=_TEX_W, height=2,
            default_value=_make_dither_strip(_TEX_W, 2))
        # live text-card font preview (updated by _refresh_tc_preview)
        dpg.add_dynamic_texture(
            width=_TC_PREVIEW_W, height=_TC_PREVIEW_H,
            default_value=[0.0] * (_TC_PREVIEW_W * _TC_PREVIEW_H * 4),
            tag="tc_preview_tex")


def _build_strip_themes():
    """Magenta header / cyan footer themes — solid-colour bands.
    Call after dpg.setup_dearpygui() (alongside _build_btn_themes)."""
    global _HEADER_THEME, _FOOTER_THEME
    with dpg.theme() as th:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,  _GL_MAGENTA)
            dpg.add_theme_color(dpg.mvThemeCol_Border,   (10, 6, 10))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (10, 6, 16))
    _HEADER_THEME = th

    with dpg.theme() as tf:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,  _GL_CYAN)
            dpg.add_theme_color(dpg.mvThemeCol_Border,   (4, 16, 16))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (8, 16, 22))
    _FOOTER_THEME = tf


def _build_header_strip():
    """GL1TCH-style magenta title bar across the top of the main window."""
    with dpg.child_window(height=32, border=False, no_scrollbar=True,
                          tag="gl_header") as hdr:
        with dpg.group(horizontal=True):
            dpg.add_text("■", tag="gl_header_icon")
            dpg.add_spacer(width=4)
            dpg.add_text("SHI O KIKU // AUTOMATCH", tag="gl_header_title")
            dpg.add_spacer(width=10)
            dpg.add_text("v0.66", tag="gl_header_version")
            dpg.add_spacer(width=-1)
    if _HEADER_THEME:
        dpg.bind_item_theme("gl_header", _HEADER_THEME)


def _build_footer_strip():
    """GL1TCH-style cyan status bar across the bottom of the main window.

    Hosts the `status` text element (live render/preview messages).
    """
    with dpg.child_window(height=28, border=False, no_scrollbar=True,
                          tag="gl_footer"):
        with dpg.group(horizontal=True):
            dpg.add_text("STATUS ::", tag="gl_footer_label")
            dpg.add_spacer(width=6)
            dpg.add_text("ready", tag="status")
    if _FOOTER_THEME:
        dpg.bind_item_theme("gl_footer", _FOOTER_THEME)


def _build_action_bar():
    dpg.add_spacer(height=8)
    dpg.add_separator()
    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        dpg.add_checkbox(label="БЕЗ МОНТАЖА",
                         tag="bypass_montage_chk",
                         default_value=False)
        dpg.add_spacer(width=12)
        dpg.add_text("— без нарезки по битам; эффекты применяются к исходнику целиком",
                     color=_GL_DIM)
    dpg.add_spacer(height=6)
    with dpg.group(horizontal=True):
        dpg.add_button(label="Preview", tag="preview_btn",
                       callback=_start_preview, width=200, height=42)
        dpg.add_spacer(width=10)
        dpg.add_button(label="Render",  tag="render_btn",
                       callback=_start_render,  width=200, height=42)
        dpg.add_spacer(width=10)
        # Stop button — hidden until a render is running
        dpg.add_button(label="Stop", tag="stop_btn",
                       callback=_cancel_render,
                       width=110, height=42,
                       enabled=False, show=False)
    dpg.add_spacer(height=4)
    # ── Single-video slice row ──
    with dpg.group(horizontal=True):
        dpg.add_button(label="Нарезка (чисто)",
                       tag="slice_clean_btn",
                       callback=_start_slice_clean,
                       width=200, height=30)
        dpg.add_spacer(width=10)
        dpg.add_button(label="Нарезка (с эффектами)",
                       tag="slice_fx_btn",
                       callback=_start_slice_fx,
                       width=200, height=30)
    dpg.add_text(
        "Требует выбранный видео-файл (кнопка 'Файл...' в секции Файлы)",
        color=_DIM, wrap=-1)

    dpg.add_spacer(height=4)
    # ── Batch render row ──
    with dpg.group(horizontal=True):
        dpg.add_button(label="Сделать 10 видео",
                       tag="batch_btn",
                       callback=_start_batch_render,
                       width=200, height=30)
        dpg.add_spacer(width=10)
        dpg.add_input_int(label="N",
                          tag="batch_count",
                          default_value=10, min_value=2, max_value=50,
                          width=110,
                          callback=_on_batch_count_change)
        dpg.add_spacer(width=10)
        dpg.add_checkbox(label="вариативный стиль",
                         tag="batch_vary_style",
                         default_value=True)

    dpg.add_spacer(height=4)
    with dpg.group(horizontal=True):
        dpg.add_button(label="Сохранить пресет",
                       callback=_save_preset_cb, width=200, height=26)
        dpg.add_spacer(width=10)
        dpg.add_button(label="Загрузить пресет",
                       callback=_load_preset_cb, width=200, height=26)
    dpg.add_spacer(height=6)
    dpg.add_progress_bar(tag="progress", default_value=0.0, width=-1, height=6)
    dpg.add_spacer(height=4)
    dpg.add_text("(данные сохраняются автоматически в automatch_last_session.json)",
                 color=_DIM, wrap=-1)


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    dpg.create_context()
    dpg.create_viewport(title="ANALOGVFX // AUTOMATCH", width=840, height=1000)
    dpg.setup_dearpygui()
    # Save on the viewport's red-X click (fires before destroy)
    try:
        dpg.set_exit_callback(_autosave_last_session)
    except Exception:
        pass

    font_id = _load_font(14)
    if font_id is not None:
        dpg.bind_font(font_id)
    logo_font = _load_font(8)        # small monospace for the ASCII eye
    _apply_theme()
    _build_btn_themes()        # must be after setup_dearpygui
    _build_strip_themes()
    _register_glitch_textures()
    _build_effect_browser_window()   # floating browser, show=False until BROWSER btn

    with dpg.window(tag="main", no_close=True, no_title_bar=True):

        # ── GL1TCH magenta header + datamosh corruption band ───────────────
        _build_header_strip()
        if _TEX_GLITCH_TOP:
            dpg.add_image(_TEX_GLITCH_TOP, width=-1, height=10)

        # ── ASCII eye logo (collapsible; collapse if it eats too much room) ──
        try:
            from logo_ascii import ASCII_LOGO
            with dpg.collapsing_header(label="LOGO // shi o kiku",
                                       default_open=True, tag="logo_hdr"):
                _lt = dpg.add_text(ASCII_LOGO, color=_GL_MAGENTA,
                                   tag="logo_ascii_txt")
                if logo_font is not None:
                    dpg.bind_item_font(_lt, logo_font)
        except Exception:
            pass

        dpg.add_spacer(height=4)

        # ── files ───────────────────────────────────────────────────────────
        _build_files_section()
        dpg.add_spacer(height=8)

        # ── effects (star of the show) ──────────────────────────────────────
        # dither sub-panel lives inside the side panel of the matrix now
        _build_effects_section()
        _rebuild_matrix()          # fill the matrix container with cells
        dpg.add_spacer(height=8)

        # ── palette ─────────────────────────────────────────────────────────
        _build_palette_section()
        dpg.add_spacer(height=8)

        # ── collapsing sections ─────────────────────────────────────────────
        _build_timeline_section()
        dpg.add_spacer(height=2)
        _build_text_cards_section()
        dpg.add_spacer(height=2)
        _build_blob_section()
        dpg.add_spacer(height=2)
        _build_mod_matrix_section()

        # ── action bar ──────────────────────────────────────────────────────
        _build_action_bar()

        # ── datamosh corruption band + GL1TCH cyan footer ───────────────────
        dpg.add_spacer(height=4)
        if _TEX_GLITCH_BOT:
            dpg.add_image(_TEX_GLITCH_BOT, width=-1, height=10)
        _build_footer_strip()

    # apply initial state themes to matrix cells
    for kind in USER_EFFECTS:
        tag = f"fx_btn_{kind}"
        if dpg.does_item_exist(tag):
            dpg.bind_item_theme(tag, _BTN_THEMES["off"])
        mix = f"fx_mix_{kind}"
        if dpg.does_item_exist(mix) and _SLIDER_DIMMED:
            dpg.bind_item_theme(mix, _SLIDER_DIMMED)
    _refresh_side_panel()

    # style the two main action buttons to be more prominent
    with dpg.theme() as _action_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (0, 110, 100))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (0, 155, 140))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  _TEAL)
            dpg.add_theme_color(dpg.mvThemeCol_Text,          (230, 240, 238))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
    for btn in ("preview_btn", "render_btn"):
        if dpg.does_item_exist(btn):
            dpg.bind_item_theme(btn, _action_theme)

    # ── auto-load last session BEFORE the viewport opens so the user
    #    immediately sees their previous values ────────────────────────────
    try:
        last = _last_session_path()
        if last.exists():
            _load_session_json(last)
            dpg.set_value("status", f"Последняя сессия загружена ({last.name})")
    except Exception:
        traceback.print_exc()

    dpg.show_viewport()
    dpg.set_primary_window("main", True)
    # start preview queue drain loop (runs every 6 frames)
    dpg.set_frame_callback(6, _drain_preview_queue)
    dpg.start_dearpygui()
    # save on exit
    _autosave_last_session()
    dpg.destroy_context()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
