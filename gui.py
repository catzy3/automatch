"""Dear PyGui control panel — collapsible per-effect rows, Random, dark theme."""
from __future__ import annotations

import os
import random
import threading
import time
from pathlib import Path

import dearpygui.dearpygui as dpg

from audio import list_devices as list_audio_devices
from capture import CameraThread, list_cameras
from modulation import SOURCES, ModRoute
from smart_random import (
    randomize_audio_routes, randomize_effect, smart_random,
)
from state import EFFECT_KINDS, EffectConfig, SharedState


RESOLUTIONS = [
    ("640 x 480",   640, 480),
    ("1280 x 720", 1280, 720),
    ("1920 x 1080",1920,1080),
]

EFFECT_LABELS = {
    "luma_key":     "Luma key",
    "motion_trails":"Motion trails",
    "heavy_grain":  "Heavy grain",
    "duotone":      "Duotone",
    "cctv_grid":    "CCTV grid",
    "quad_split":   "Quad split",
    "hud_overlay":  "HUD overlay",
    "bloom_glow":   "Bloom glow",
    "dual_tone":    "Dual tone",
    "fragment_layers": "Fragment layers",
    "pixel_sort":   "Pixel sort (row smear)",
    "wave_warp":    "Wave warp",
    "ascii_mosaic": "ASCII mosaic",
    "depth_save":   "Depth save (marker)",
    "depth_apply":  "Depth apply (mask)",
    "depth_view":   "Depth view (heatmap)",
    "datamosh":     "Datamosh",
    "pixel_swap":   "Pixel swap",
    "mirror_flow":  "Mirror flow",
    "vhs":          "VHS",
    "rgb_split":    "RGB split",
    "glitch":       "Glitch (macroblock)",
    "crt":          "CRT",
    "drip":         "Drip",
    "dither":       "Dither (circuit mod.)",
}

# Effects whose dry/wet (mix) slider is hidden from the pipeline panel.
# The shader for these is expected to ignore u_mix and always render full
# strength — they're "on" or "off", no in-between.
EFFECTS_NO_MIX = {"dither"}

# Parameters grouped per effect (label, tag, vmin, vmax).
# vmin/vmax of None = boolean (rendered as a checkbox via int 0/1 slider).
EFFECT_PARAMS = {
    "luma_key": [
        ("Lift",   "luma_lift",   -0.3,  0.3),
        ("Gain",   "luma_gain",    0.3,  2.5),
        ("Gamma",  "luma_gamma",   0.3,  3.0),
        ("Invert", "luma_invert",  0.0,  1.0),
    ],
    "motion_trails": [
        ("Decay",       "trails_decay",     0.5, 0.999),
        ("Threshold",   "trails_threshold", 0.0, 1.0),
        ("Blur",        "trails_blur",      0.0, 1.0),
        ("Drift X",     "trails_drift_x",   -1.0, 1.0),
        ("Drift Y",     "trails_drift_y",   -1.0, 1.0),
        ("Tint",        ("trails_tint_r", "trails_tint_g", "trails_tint_b"), 0.0, 1.0),
        ("Secondary",   "trails_secondary", 0.0, 1.0),
        ("Mode (0-4)",  "trails_mode",      0.0, 4.0),
        ("Polarity",    "trails_polarity",  0.0, 1.0),
        ("Chaos",       "trails_chaos",     0.0, 1.0),
    ],
    "heavy_grain": [
        ("Amount", "grain_amount", 0.0, 1.5),
        ("Size",   "grain_size",   0.0, 1.0),
        ("Chroma", "grain_chroma", 0.0, 1.0),
        ("Blotch", "grain_blotch", 0.0, 1.0),
    ],
    "duotone": [
        ("Accent",   ("accent_r", "accent_g", "accent_b"), 0.0, 1.0),
        ("Midpoint", "duotone_midpoint",0.0, 1.0),
        ("Strength", "duotone_strength",0.0, 1.0),
    ],
    "cctv_grid": [
        ("Cols",         "cctv_cols",             1.0, 4.0),
        ("Rows",         "cctv_rows",             1.0, 4.0),
        ("Gap",          "cctv_gap",              0.0, 0.05),
        ("Invert mask",  "cctv_invert_mask",      0.0, 65535.0),
        ("Delay mask",   "cctv_delay_mask",       0.0, 4294967.0),
        ("Tint strength","cctv_tint_strength",    0.0, 1.0),
        ("Desaturate",   "cctv_desat",            0.0, 1.0),
        ("Label glow",   "cctv_label_brightness", 0.0, 2.0),
    ],
    "quad_split": [
        ("Gap",         "quad_gap",         0.0, 0.05),
        ("Invert mask", "quad_invert_mask", 0.0, 15.0),
        ("Freeze",      "quad_freeze",      0.0, 1.0),
    ],
    "hud_overlay": [
        ("Brightness",     "hud_brightness",      0.0, 3.0),
        ("Line thickness", "hud_line_thickness",  1.0, 6.0),
    ],
    "bloom_glow": [
        ("Threshold", "bloom_threshold", 0.0, 1.0),
        ("Radius",    "bloom_radius",    0.0, 1.0),
        ("Intensity", "bloom_intensity", 0.0, 3.0),
        ("Tint",      ("bloom_tint_r", "bloom_tint_g", "bloom_tint_b"), 0.0, 1.0),
    ],
    "dual_tone": [
        ("Shadow",   ("dual_shadow_r", "dual_shadow_g", "dual_shadow_b"), 0.0, 1.0),
        ("Mid",      ("dual_mid_r",    "dual_mid_g",    "dual_mid_b"),    0.0, 1.0),
        ("Highlight",("dual_high_r",   "dual_high_g",   "dual_high_b"),   0.0, 1.0),
        ("Split",    "dual_split",    0.0, 1.0),
        ("Strength", "dual_strength", 0.0, 1.0),
    ],
    "fragment_layers": [
        ("Count",  "frag_count",  0.0, 1.0),
        ("Height", "frag_height", 0.0, 1.0),
        ("Offset", "frag_offset", 0.0, 1.0),
        ("Alpha",  "frag_alpha",  0.0, 1.0),
    ],
    "pixel_sort": [
        ("Axis (0=H 1=V)", "psort_axis",         0.0, 1.0),
        ("Algorithm",      "psort_algorithm",    0.0, 3.0),
        ("Color mode",     "psort_color_mode",   0.0, 4.0),
        ("Threshold",      "psort_threshold",    0.0, 1.0),
        ("Length",         "psort_length",       0.0, 1.0),
        ("Length variance","psort_length_var",   0.0, 1.0),
        ("Direction",      "psort_direction",   -1.0, 1.0),
        ("Band density",   "psort_band_density", 0.0, 1.0),
        ("Band jitter",    "psort_band_jitter",  0.0, 1.0),
        ("Band speed",     "psort_band_speed",   0.0, 1.0),
        ("Edge fade",      "psort_edge_fade",    0.0, 1.0),
        ("Chromatic",      "psort_chromatic",    0.0, 1.0),
        ("Blend mode",     "psort_blend",        0.0, 3.0),
    ],
    "wave_warp": [
        # global
        ("Angle",       "wave_angle",       0.0, 1.0),
        ("Polar",       "wave_polar",       0.0, 1.0),
        ("Chromatic",   "wave_chromatic",   0.0, 2.0),
        ("Band height", "wave_band_height", 0.01,1.0),
        ("Band y",      "wave_band_y",      0.0, 1.0),
        ("Band soft",   "wave_band_soft",   0.0, 1.0),
        # wave 1
        ("Amp 1",   "wave_amp1",   0.0, 2.0),
        ("Freq 1",  "wave_freq1",  0.0, 120.0),
        ("Phase 1", "wave_phase1", 0.0, 6.28),
        ("Speed 1", "wave_speed1", -4.0, 4.0),
        ("Shape 1", "wave_shape1", 0.0, 4.0),
        # wave 2
        ("Amp 2",   "wave_amp2",   0.0, 2.0),
        ("Freq 2",  "wave_freq2",  0.0, 120.0),
        ("Phase 2", "wave_phase2", 0.0, 6.28),
        ("Speed 2", "wave_speed2", -4.0, 4.0),
        ("Shape 2", "wave_shape2", 0.0, 4.0),
        # wave 3
        ("Amp 3",   "wave_amp3",   0.0, 2.0),
        ("Freq 3",  "wave_freq3",  0.0, 120.0),
        ("Phase 3", "wave_phase3", 0.0, 6.28),
        ("Speed 3", "wave_speed3", -4.0, 4.0),
        ("Shape 3", "wave_shape3", 0.0, 4.0),
    ],
    "ascii_mosaic": [
        ("Cell size",    "ascii_cell_size",    0.0, 1.0),
        ("Invert",       "ascii_invert",       0.0, 1.0),
        ("Color source", "ascii_color_source", 0.0, 1.0),
        ("Tint",         ("ascii_tint_r", "ascii_tint_g", "ascii_tint_b"), 0.0, 1.0),
        # text input handled separately by _make_text_input
        ("Glyphs",       "ascii_text",         None, None),
    ],
    "depth_save": [],
    "depth_apply": [
        ("Depth min", "depth_min",     0.0, 1.0),
        ("Depth max", "depth_max",     0.0, 1.0),
        ("Feather",   "depth_feather", 0.0, 0.5),
        ("Invert",    "depth_invert",  0.0, 1.0),
    ],
    "depth_view": [
        ("Mode",   "depth_view_mode",   0.0, 2.0),
        ("Gain",   "depth_view_gain",   0.0, 4.0),
        ("Offset", "depth_view_offset",-0.5, 0.5),
        ("Invert", "depth_view_invert", 0.0, 1.0),
    ],
    "datamosh": [
        ("Strength",     "datamosh_strength",     0.0, 1.5),
        ("Freeze rate",  "datamosh_freeze",       0.0, 1.0),
        ("Period (s)",   "datamosh_period",       0.5, 10.0),
        ("Breakthrough", "datamosh_breakthrough", 0.0, 1.0),
        ("Pixel jitter", "datamosh_jitter",       0.0, 1.0),
        ("Chaos",        "datamosh_chaos",        0.0, 1.0),
    ],
    "pixel_swap": [
        ("Threshold",   "pixel_swap_threshold",   0.0, 1.0),
        ("Radius",      "pixel_swap_radius",      0.0, 1.0),
        ("Chaos",       "pixel_swap_chaos",       0.0, 1.0),
        ("Directional", "pixel_swap_directional", 0.0, 1.0),
        ("Temporal",    "pixel_swap_temporal",    0.0, 1.0),
    ],
    "mirror_flow": [
        ("Drip speed", "mirror_drip",   0.0, 0.05),
        ("Decay",      "mirror_decay",  0.5, 0.999),
        ("Unroll",     "mirror_unroll", 0.0, 1.0),
        ("Axis H",     "mirror_axis_h", 0.0, 1.0),
        ("Axis V",     "mirror_axis_v", 0.0, 1.0),
        ("Swirl",      "mirror_swirl",  0.0, 1.0),
        ("Chaos",      "mirror_chaos",  0.0, 1.0),
    ],
    "vhs": [
        ("Tracking noise", "vhs_tracking_noise", 0.0, 1.0),
        ("Line jitter",    "vhs_line_jitter",    0.0, 1.0),
        ("Chroma blur",    "vhs_chroma_blur",    0.0, 1.0),
        ("Color loss",     "vhs_color_loss",     0.0, 1.0),
        ("Head switch",    "vhs_head_switch",    0.0, 1.0),
    ],
    "rgb_split": [
        ("Shift amount",  "rgb_shift_amount",  0.0, 1.0),
        ("Wave frequency","rgb_wave_frequency",0.0, 40.0),
        ("Wave speed",    "rgb_wave_speed",    0.0, 6.0),
    ],
    "glitch": [
        ("Block size",    "glitch_block_size",    0.0, 1.0),
        ("Drop chance",   "glitch_drop_chance",   0.0, 1.0),
        ("Repeat chance", "glitch_repeat_chance", 0.0, 1.0),
    ],
    "crt": [
        ("Scanlines",    "crt_scanline_strength", 0.0, 1.0),
        ("RGB mask",     "crt_mask_strength",     0.0, 1.0),
        ("Curvature",    "crt_curvature",         0.0, 0.6),
        ("Vignette",     "crt_vignette",          0.0, 1.0),
        ("Persistence",  "crt_persistence",       0.0, 1.0),
    ],
    "drip": [
        ("Drip speed", "drip_speed", 0.0, 0.05),
        ("Decay",      "drip_decay", 0.5, 0.999),
    ],
    "dither": [
        # ── quantisation ──
        ("Levels",        "dither_levels",          2.0,  32.0),
        ("Grid size px",  "dither_grid_size",       1.0,  32.0),
        # ── oscillator A ──
        ("Osc A freq",    "dither_osc_a_freq",      0.5,  50.0),
        ("Osc A speed",   "dither_osc_a_speed",     0.0,   3.0),
        # ── oscillator B ──
        ("Osc B freq",    "dither_osc_b_freq",      0.5,  50.0),
        ("Osc B speed",   "dither_osc_b_speed",     0.0,   3.0),
        # ── combination ──
        ("Mix: avg/mul/xor/cross", "dither_mix_mode",  0.0,  3.0),
        ("Trace thickness",        "dither_trace_thickness", 0.0,  0.5),
        ("Jitter",                 "dither_jitter",          0.0,  1.0),
        ("Chromatic phase",        "dither_chromatic",       0.0,  1.0),
        ("Polarity",               "dither_polarity",        0.0,  1.0),
        # ── output ──
        ("Mode: mono/chan/duo", "dither_color_mode",     0.0,  2.0),
        ("Color A (shadow)",
            ("dither_color_a_r", "dither_color_a_g", "dither_color_a_b"),
            0.0, 1.0),
        ("Color B (highlight)",
            ("dither_color_b_r", "dither_color_b_g", "dither_color_b_b"),
            0.0, 1.0),
    ],
}

# Global (non-effect) parameters shown in the top "global" expander
GLOBAL_PARAMS = [
    ("Gain",       "gain",       0.0, 2.0),
    ("Contrast",   "contrast",   0.0, 2.5),
    ("Saturation", "saturation", 0.0, 2.5),
]


# ---------------------------------------------------------------- helpers ----

def _load_cyrillic_font(size: int = 15):
    candidates = [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            with dpg.font_registry():
                with dpg.font(path, size) as f:
                    pass
                return f
    return None


def _apply_dark_theme():
    accent     = (255, 110, 35)
    accent_hov = (255, 145, 75)
    bg         = (15, 15, 18)
    panel      = (24, 24, 28)
    frame      = (34, 34, 40)
    text       = (235, 220, 200)
    text_dim   = (160, 150, 135)

    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, bg)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, panel)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, frame)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (50, 40, 36))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,  (70, 50, 40))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (52, 32, 18))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, accent_hov)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  accent)
            dpg.add_theme_color(dpg.mvThemeCol_Header, (45, 28, 14))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, accent_hov)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,  accent)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, accent)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, accent_hov)
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, accent)
            dpg.add_theme_color(dpg.mvThemeCol_Text, text)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, text_dim)
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (60, 50, 40))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (70, 55, 40))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, panel)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (50, 32, 18))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing, 18)
    dpg.bind_theme(theme)


def _accent_button_theme():
    """Theme for highlighted action buttons (Start/REC/Random)."""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 90, 25))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (240, 130, 50))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 165, 80))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (15, 15, 18))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
    return t


# ---------------------------------------------------------------- main ----

def build_gui(state: SharedState, capture_thread: CameraThread,
              on_record_toggle=None, audio_thread=None,
              param_ranges: dict | None = None) -> None:
    dpg.create_context()
    # ASCII-only title because Windows viewport title goes through Win32 API
    # which mojibakes non-ASCII bytes.
    dpg.create_viewport(title="AnalogVFX - control", width=520, height=940,
                        disable_close=True)
    dpg.setup_dearpygui()

    # When user clicks the X, we intercept and show a "Save as preset?" dialog.
    def on_viewport_close():
        _show_quit_dialog(state)
    try:
        dpg.set_viewport_close_callback(on_viewport_close)
    except Exception:
        # not all DPG versions expose this — fall back to Quit button only
        pass

    font_id = _load_cyrillic_font(15)
    if font_id is not None:
        dpg.bind_font(font_id)
    _apply_dark_theme()
    accent_btn = _accent_button_theme()

    cams_cache = {"items": list_cameras()}
    cam_labels = [f"{i}: {name}" for i, name in cams_cache["items"]] \
                 or ["(none — enter index manually)"]
    cam_index_map = {f"{i}: {name}": i for i, name in cams_cache["items"]}
    res_labels = [r[0] for r in RESOLUTIONS]

    # ---------- callbacks for controls ----------

    def on_start(_s, _a):
        state.update_param("running", True)

    def on_stop(_s, _a):
        state.update_param("running", False)

    def on_device(_s, val):
        idx = cam_index_map.get(val)
        if idx is not None:
            state.update_param("device_index", idx)
            capture_thread.request_restart()

    def on_manual_index(_s, val):
        try:
            state.update_param("device_index", int(val))
            capture_thread.request_restart()
        except (TypeError, ValueError):
            pass

    def on_rescan(_s, _a):
        cams_cache["items"] = list_cameras()
        new_labels = [f"{i}: {name}" for i, name in cams_cache["items"]] \
                     or ["(none — enter index manually)"]
        cam_index_map.clear()
        cam_index_map.update({f"{i}: {name}": i for i, name in cams_cache["items"]})
        dpg.configure_item("camera_combo", items=new_labels,
                           default_value=new_labels[0])

    def on_resolution(_s, val):
        for label, w, h in RESOLUTIONS:
            if label == val:
                state.update_param("width", w)
                state.update_param("height", h)
                capture_thread.request_restart()
                break

    def on_sender_name(_s, val):
        state.update_param("sender_name", str(val))

    def on_record(_s, _a):
        cur = state.snapshot_params().recording
        new = not cur
        state.update_param("recording", new)
        if on_record_toggle is not None:
            on_record_toggle(new)
        dpg.configure_item("rec_btn", label="■ STOP" if new else "● REC")

    # ---------- presets / reset / random ----------

    def _all_param_tags():
        """Flatten: color tuples (r, g, b) become 3 entries, text inputs
        (with None bounds) are skipped because they aren't numeric."""
        flat = list(GLOBAL_PARAMS)
        for group in EFFECT_PARAMS.values():
            for label, tag, vmin, vmax in group:
                if isinstance(tag, tuple):
                    for t in tag:
                        flat.append((label, t, 0.0, 1.0))
                elif vmin is None or vmax is None:
                    # text input — not a numeric param
                    continue
                else:
                    flat.append((label, tag, vmin, vmax))
        return flat

    def on_save_preset(_s, _a):
        dpg.show_item("preset_save_dialog")

    def on_load_preset(_s, _a):
        dpg.show_item("preset_load_dialog")

    def on_reset(_s, _a):
        from state import Params
        defaults = Params()
        for label, tag, vmin, vmax in _all_param_tags():
            val = getattr(defaults, tag)
            state.update_param(tag, val)
            if dpg.does_item_exist(f"sl_{tag}"):
                dpg.set_value(f"sl_{tag}", val)

    def _push_to_slider(tag, val):
        sl = f"sl_{tag}"
        if dpg.does_item_exist(sl):
            dpg.set_value(sl, val)

    def _refresh_all_sliders():
        params = state.snapshot_params()
        # 1) regular sliders
        for label, tag, vmin, vmax in _all_param_tags():
            if hasattr(params, tag) and dpg.does_item_exist(f"sl_{tag}"):
                dpg.set_value(f"sl_{tag}", getattr(params, tag))
        # 2) color pickers (one per color group)
        for group in EFFECT_PARAMS.values():
            for label, tag, vmin, vmax in group:
                if isinstance(tag, tuple):
                    r_tag, g_tag, b_tag = tag
                    color_tag = f"color_{r_tag[:-2]}"
                    if dpg.does_item_exist(color_tag):
                        dpg.set_value(color_tag, [
                            int(getattr(params, r_tag, 0.0) * 255),
                            int(getattr(params, g_tag, 0.0) * 255),
                            int(getattr(params, b_tag, 0.0) * 255),
                            255,
                        ])
                elif vmin is None and vmax is None:
                    # text input
                    txt_tag = f"txt_{tag}"
                    if dpg.does_item_exist(txt_tag):
                        dpg.set_value(txt_tag, str(getattr(params, tag, "")))

    def on_random(_s, _a):
        smart_random(state, set_value_cb=_push_to_slider)
        _build_pipeline_panel(state)

    def on_random_audio(_s, _a):
        if param_ranges is None:
            print("[gui] no param_ranges supplied — can't randomize audio routes")
            return
        randomize_audio_routes(state, param_ranges, max_routes=4)
        _build_pipeline_panel(state)   # refreshes ~/● buttons


    def preset_save_cb(_s, app_data):
        path = Path(app_data["file_path_name"])
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        state.save_preset(path)

    def preset_load_cb(_s, app_data):
        path = Path(app_data["file_path_name"])
        state.load_preset(path)
        # After load, re-seed base_params from the freshly-loaded params so the
        # modulation engine doesn't keep modulating against stale bases.
        params = state.snapshot_params()
        for label, tag, vmin, vmax in _all_param_tags():
            if hasattr(params, tag):
                state.update_param(tag, getattr(params, tag))
            if dpg.does_item_exist(f"sl_{tag}"):
                dpg.set_value(f"sl_{tag}", getattr(params, tag))
        _build_pipeline_panel(state)

    with dpg.file_dialog(directory_selector=False, show=False,
                         callback=preset_save_cb, tag="preset_save_dialog",
                         default_filename="preset", width=540, height=420):
        dpg.add_file_extension(".json")

    with dpg.file_dialog(directory_selector=False, show=False,
                         callback=preset_load_cb, tag="preset_load_dialog",
                         width=540, height=420):
        dpg.add_file_extension(".json")

    # "Save as preset and quit" — a parallel save dialog that exits after save
    def quit_save_cb(_s, app_data):
        path = Path(app_data["file_path_name"])
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            state.save_preset(path)
            print(f"[gui] preset saved → {path}")
        except Exception as e:
            print(f"[gui] preset save failed: {e}")
        dpg.stop_dearpygui()

    with dpg.file_dialog(directory_selector=False, show=False,
                         callback=quit_save_cb, tag="quit_save_dialog",
                         default_filename="my_preset", width=540, height=420):
        dpg.add_file_extension(".json")

    # ---------- main window ----------

    with dpg.window(label="AnalogVFX", tag="main_window", no_close=True):

        # toolbar
        with dpg.group(horizontal=True):
            b_start = dpg.add_button(label="▶ Старт", callback=on_start, width=82)
            b_stop  = dpg.add_button(label="■ Стоп",  callback=on_stop,  width=82)
            b_rec   = dpg.add_button(label="● REC",   callback=on_record,
                                      width=82, tag="rec_btn")
            b_rand  = dpg.add_button(label="✦ Random", callback=on_random, width=92)
            b_reset = dpg.add_button(label="↺ Reset",  callback=on_reset,  width=82)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save preset", callback=on_save_preset, width=110)
            dpg.add_button(label="Load preset", callback=on_load_preset, width=110)
            dpg.add_button(label="⏻ Quit", width=82,
                           callback=lambda _s, _a: _show_quit_dialog(state))

        dpg.add_button(label="🎵 Random audio routes (≤4)",
                       callback=on_random_audio, width=-1)

        # global audio pulse — visible all the time so you know audio's flowing
        dpg.add_progress_bar(tag="global_pulse", default_value=0.0,
                              width=-1, overlay="PULSE 0.00")

        # accent theme for the action buttons
        for b in (b_start, b_rec, b_rand):
            dpg.bind_item_theme(b, accent_btn)

        dpg.add_separator()

        # ---------- source ----------
        with dpg.collapsing_header(label="Источник", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_combo(items=cam_labels, default_value=cam_labels[0],
                              label="Camera", callback=on_device, width=240,
                              tag="camera_combo")
                dpg.add_button(label="Rescan", callback=on_rescan)
            dpg.add_input_int(label="Manual index", default_value=0,
                              callback=on_manual_index, width=120,
                              min_value=0, max_value=10)
            dpg.add_combo(items=res_labels, default_value="1280 x 720",
                          label="Resolution", callback=on_resolution, width=300)
            dpg.add_input_text(default_value=state.params.sender_name,
                               callback=on_sender_name, width=300,
                               label="Spout sender",
                               tag="sender_name_input")

        # ---------- global tone ----------
        with dpg.collapsing_header(label="Global tone", default_open=False):
            for label, tag, vmin, vmax in GLOBAL_PARAMS:
                _make_slider(state, label, tag, vmin, vmax)

        # ---------- Detection sensitivity ----------
        with dpg.collapsing_header(label="Detection — чувствительность", default_open=False):
            def make_bool_cb(tag):
                def cb(_s, val):
                    state.update_param(tag, bool(val))
                return cb

            def make_int_cb(tag):
                def cb(_s, val):
                    state.update_param(tag, int(val))
                return cb

            dpg.add_slider_float(
                label="Min confidence", default_value=state.params.detect_min_confidence,
                min_value=0.20, max_value=0.95,
                callback=lambda _s, v: state.update_param("detect_min_confidence", float(v)),
                width=240,
            )
            dpg.add_slider_float(
                label="Eye scale factor", default_value=state.params.detect_eye_sf,
                min_value=1.02, max_value=1.40,
                callback=lambda _s, v: state.update_param("detect_eye_sf", float(v)),
                width=240,
            )
            dpg.add_slider_int(
                label="Eye min neighbors", default_value=state.params.detect_eye_neighbors,
                min_value=2, max_value=12,
                callback=make_int_cb("detect_eye_neighbors"), width=240,
            )
            dpg.add_slider_int(
                label="Detect every N frames", default_value=state.params.detect_skip_frames,
                min_value=1, max_value=10,
                callback=make_int_cb("detect_skip_frames"), width=240,
            )
            dpg.add_separator()
            dpg.add_text("Включить детекторы:")
            with dpg.group(horizontal=True):
                dpg.add_checkbox(label="Face", default_value=state.params.detect_face,
                                 callback=make_bool_cb("detect_face"))
                dpg.add_checkbox(label="Eyes (priority)", default_value=state.params.detect_eyes,
                                 callback=make_bool_cb("detect_eyes"))
                dpg.add_checkbox(label="Pose", default_value=state.params.detect_pose,
                                 callback=make_bool_cb("detect_pose"))
            with dpg.group(horizontal=True):
                dpg.add_checkbox(label="Face mesh", default_value=state.params.detect_face_mesh,
                                 callback=make_bool_cb("detect_face_mesh"))
                dpg.add_checkbox(label="Hands", default_value=state.params.detect_hands,
                                 callback=make_bool_cb("detect_hands"))
                dpg.add_checkbox(label="Depth", default_value=state.params.depth_enabled,
                                 callback=make_bool_cb("depth_enabled"))
            dpg.add_text("Depth: при первом включении скачается ~50MB ONNX, иначе fallback по лицам.",
                         color=(160, 150, 135))

            # ---- Blob tracking ----
            dpg.add_separator()
            dpg.add_text("Blob tracking — детекция по цвету")
            dpg.add_checkbox(label="Enable blobs",
                             default_value=state.params.detect_blobs,
                             callback=make_bool_cb("detect_blobs"))
            # target color picker (combines blob_target_r/g/b)
            _make_color_picker(state, "Target color",
                               ("blob_target_r", "blob_target_g", "blob_target_b"),
                               width=200)
            dpg.add_slider_float(label="Hue tolerance",
                                 default_value=state.params.blob_hue_tol,
                                 min_value=2.0, max_value=60.0,
                                 callback=lambda _s, v: state.update_param("blob_hue_tol", float(v)),
                                 width=240, tag="sl_blob_hue_tol")
            dpg.add_slider_float(label="Sat min",
                                 default_value=state.params.blob_sat_min,
                                 min_value=0.0, max_value=1.0,
                                 callback=lambda _s, v: state.update_param("blob_sat_min", float(v)),
                                 width=240, tag="sl_blob_sat_min")
            dpg.add_slider_float(label="Val min",
                                 default_value=state.params.blob_val_min,
                                 min_value=0.0, max_value=1.0,
                                 callback=lambda _s, v: state.update_param("blob_val_min", float(v)),
                                 width=240, tag="sl_blob_val_min")
            dpg.add_slider_float(label="Area min (px)",
                                 default_value=state.params.blob_area_min,
                                 min_value=20.0, max_value=20000.0,
                                 callback=lambda _s, v: state.update_param("blob_area_min", float(v)),
                                 width=240, tag="sl_blob_area_min")
            dpg.add_slider_float(label="Area max (px)",
                                 default_value=state.params.blob_area_max,
                                 min_value=1000.0, max_value=400000.0,
                                 callback=lambda _s, v: state.update_param("blob_area_max", float(v)),
                                 width=240, tag="sl_blob_area_max")
            dpg.add_slider_int(label="Max blobs",
                               default_value=state.params.blob_max_count,
                               min_value=1, max_value=32,
                               callback=lambda _s, v: state.update_param("blob_max_count", int(v)),
                               width=240, tag="sl_blob_max_count")

        # ---------- HUD overlay toggles ----------
        with dpg.collapsing_header(label="HUD overlay: что показывать", default_open=False):
            def make_bool_cb_hud(tag):
                def cb(_s, val):
                    state.update_param(tag, bool(val))
                return cb
            with dpg.group(horizontal=True):
                dpg.add_checkbox(label="Face box",
                                 default_value=state.params.hud_show_face_box,
                                 callback=make_bool_cb_hud("hud_show_face_box"))
                dpg.add_checkbox(label="Face KP",
                                 default_value=state.params.hud_show_face_keypoints,
                                 callback=make_bool_cb_hud("hud_show_face_keypoints"))
                dpg.add_checkbox(label="Face mesh",
                                 default_value=state.params.hud_show_face_mesh,
                                 callback=make_bool_cb_hud("hud_show_face_mesh"))
            with dpg.group(horizontal=True):
                dpg.add_checkbox(label="Eyes",
                                 default_value=state.params.hud_show_eyes,
                                 callback=make_bool_cb_hud("hud_show_eyes"))
                dpg.add_checkbox(label="Pupils",
                                 default_value=state.params.hud_show_pupils,
                                 callback=make_bool_cb_hud("hud_show_pupils"))
                dpg.add_checkbox(label="Hands",
                                 default_value=state.params.hud_show_hands,
                                 callback=make_bool_cb_hud("hud_show_hands"))
                dpg.add_checkbox(label="Blobs",
                                 default_value=state.params.hud_show_blobs,
                                 callback=make_bool_cb_hud("hud_show_blobs"))
            with dpg.group(horizontal=True):
                dpg.add_checkbox(label="Pose",
                                 default_value=state.params.hud_show_pose,
                                 callback=make_bool_cb_hud("hud_show_pose"))
                dpg.add_checkbox(label="Vectors",
                                 default_value=state.params.hud_show_vectors,
                                 callback=make_bool_cb_hud("hud_show_vectors"))
                dpg.add_checkbox(label="Text",
                                 default_value=state.params.hud_show_text,
                                 callback=make_bool_cb_hud("hud_show_text"))
                dpg.add_checkbox(label="Cross",
                                 default_value=state.params.hud_show_crosshairs,
                                 callback=make_bool_cb_hud("hud_show_crosshairs"))

            # ---- custom HUD text ----
            dpg.add_separator()
            dpg.add_text("Свой текст (placeholders: %TC% %FRAME% %WIDTH% %HEIGHT% %FACES%)")
            for label, tag in [
                ("Top-Left",     "hud_text_top_left"),
                ("Top-Right",    "hud_text_top_right"),
                ("Bottom-Left",  "hud_text_bottom_left"),
                ("Bottom-Right", "hud_text_bottom_right"),
                ("Face label",   "hud_text_face_label"),
                ("Blob label",   "hud_text_blob_label"),
            ]:
                _make_text_input(state, label, tag, width=240)
            dpg.add_text("Face label: %INDEX% %CONF%",
                         color=(160, 150, 135))
            dpg.add_text("Blob label: %INDEX% %AREA% %X% %Y% %W% %H%",
                         color=(160, 150, 135))

        # ---------- Audio level meters + source picker ----------
        with dpg.collapsing_header(label="Audio reactivity", default_open=True):
            # source picker
            devs = list_audio_devices()
            # build label → (index, kind) map
            audio_choice_map: dict[str, tuple[int, str]] = {}
            audio_labels: list[str] = []
            for idx, name, kind in devs:
                tag = "MIC" if kind == "input" else "OUT (loopback)"
                lbl = f"{idx}: [{tag}] {name}"
                audio_choice_map[lbl] = (idx, kind)
                audio_labels.append(lbl)
            if not audio_labels:
                audio_labels = ["(no audio devices)"]

            def on_audio_device(_s, val):
                if audio_thread is None:
                    return
                pair = audio_choice_map.get(val)
                if not pair:
                    return
                idx, kind = pair
                loopback = (kind == "output")
                audio_thread.reconfigure(device_index=idx, loopback=loopback)
                dpg.set_value("audio_loopback_chk", loopback)

            def on_loopback_chk(_s, val):
                if audio_thread is None:
                    return
                # rerun with current device but new loopback flag
                cur = dpg.get_value("audio_device_combo")
                pair = audio_choice_map.get(cur)
                idx = pair[0] if pair else None
                audio_thread.reconfigure(device_index=idx, loopback=bool(val))

            dpg.add_combo(items=audio_labels,
                          default_value=audio_labels[0],
                          label="Source", callback=on_audio_device,
                          width=320, tag="audio_device_combo")
            dpg.add_checkbox(label="WASAPI loopback (capture system audio)",
                             default_value=False, callback=on_loopback_chk,
                             tag="audio_loopback_chk")
            dpg.add_text("Подсказка: чтобы захватывать звук из браузера/плеера,",
                         color=(160, 150, 135))
            dpg.add_text("выбери в Source устройство [OUT (loopback)] (например динамики).",
                         color=(160, 150, 135))

            dpg.add_separator()
            for band in ("bass", "mid", "hi", "peak"):
                dpg.add_slider_float(
                    label=band.upper(), tag=f"audio_meter_{band}",
                    default_value=0.0, min_value=0.0, max_value=1.0,
                    width=240, format="%.2f",
                )

            # No manual frame callback — a dedicated poll thread (started just
            # before dpg.start_dearpygui) updates these meters along with all
            # modulation indicators.

        dpg.add_separator()
        dpg.add_text("Цепочка эффектов (стрелка = развернуть параметры)")
        with dpg.group(tag="pipeline_rows"):
            pass
        _build_pipeline_panel(state)

    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

    # ---- live UI updater (separate thread; dpg.set_value is thread-safe) ----
    poll = GuiPollThread(state)
    poll.start()
    try:
        dpg.start_dearpygui()
    finally:
        poll.stop()
        dpg.destroy_context()


class GuiPollThread(threading.Thread):
    """Polls SharedState ~25 Hz and pushes live values into UI widgets:
       - audio meters (BASS/MID/HI/PEAK)
       - per-param effective-value bars (under each slider)
       - global toolbar audio pulse
       - active-mod button indicator (~ becomes ●)
    """

    def __init__(self, state: SharedState) -> None:
        super().__init__(name="GuiPollThread", daemon=True)
        self.state = state
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set() and not self.state.stop_event.is_set():
            try:
                self._tick()
            except Exception:
                # never let the UI poller die from a transient widget issue
                pass
            # 15 Hz — enough for visible smoothness, easy on CPU
            time.sleep(0.066)

    def _tick(self) -> None:
        # ---- audio meters ----
        bands = self.state.get_audio_bands()
        for b, v in bands.items():
            tag = f"audio_meter_{b}"
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, float(v))
        # global toolbar pulse — size driven by 'peak'
        pulse_tag = "global_pulse"
        if dpg.does_item_exist(pulse_tag):
            peak = float(bands.get("peak", 0.0))
            dpg.configure_item(pulse_tag,
                               default_value=peak,
                               overlay=f"PULSE {peak:.2f}")
        # ---- mod-popup signal indicator (audio + tracking) ----
        if dpg.does_item_exist("mod_popup_signal") and dpg.does_item_exist("mod_popup_src"):
            try:
                src = dpg.get_value("mod_popup_src")
                combined = dict(bands)
                combined.update(self.state.get_tracking())
                if src in combined:
                    dpg.configure_item("mod_popup_signal",
                                       default_value=float(combined[src]),
                                       overlay=f"{src.upper()} {combined[src]:.2f}")
            except Exception:
                pass

        # ---- effective-value bars under each slider ----
        params = self.state.snapshot_params()
        base = self.state.snapshot_base_params()
        active_targets = {r.target for r in self.state.snapshot_mod_routes()
                          if r.source != "off"}

        # map effect kind → current mix for quick lookup
        kind_to_mix = {e.kind: e.mix for e in params.effects}

        for tag in list(base.keys()):
            bar = f"mod_bar_{tag}"
            if not dpg.does_item_exist(bar):
                continue
            try:
                vmin, vmax = dpg.get_item_user_data(bar) or (0.0, 1.0)
                if tag.startswith("effect_mix_"):
                    kind = tag[len("effect_mix_"):]
                    eff = kind_to_mix.get(kind, base[tag])
                else:
                    eff = getattr(params, tag, base[tag])
                normalized = (eff - vmin) / max(vmax - vmin, 1e-6)
                normalized = max(0.0, min(1.0, float(normalized)))
                dpg.set_value(bar, normalized)
            except Exception:
                pass

            # update ~ button label so it shows when route is active
            btn = f"mod_btn_{tag}"
            if dpg.does_item_exist(btn):
                want_label = "●" if tag in active_targets else "~"
                try:
                    cur_label = dpg.get_item_label(btn)
                    if cur_label != want_label:
                        dpg.configure_item(btn, label=want_label)
                except Exception:
                    pass


# -------- per-effect collapsible row construction --------

def _make_param_widget(state: SharedState, label: str, tag, vmin, vmax,
                       width: int = 230) -> None:
    """Dispatcher: chooses widget type based on `tag` shape.
       - tuple of 3 strings → color picker
       - string + vmin/vmax both None → text input
       - string + numeric range → slider (with `~` mod button)
    """
    if isinstance(tag, tuple) and len(tag) == 3:
        _make_color_picker(state, label, tag, width=width)
    elif vmin is None and vmax is None:
        _make_text_input(state, label, tag, width=width)
    else:
        _make_slider(state, label, tag, vmin, vmax, width=width)


def _make_slider(state: SharedState, label: str, tag: str,
                 vmin: float, vmax: float, width: int = 230) -> None:
    """Slider showing BASE value (what the user set) plus a `~` mod button."""
    base = state.snapshot_base_params()
    current = base.get(tag, getattr(state.params, tag))

    def cb(_s, val):
        state.update_param(tag, float(val))

    with dpg.group():
        with dpg.group(horizontal=True):
            dpg.add_slider_float(
                label=label, tag=f"sl_{tag}",
                default_value=float(current), min_value=vmin, max_value=vmax,
                callback=cb, width=width,
            )
            mod_btn = dpg.add_button(label="~", width=22,
                                     tag=f"mod_btn_{tag}",
                                     user_data=tag)

            def open_popup(_s, _a, ud=tag):
                _open_mod_popup(state, ud, vmin, vmax)
            dpg.set_item_callback(mod_btn, open_popup)

        normalized = (current - vmin) / max(vmax - vmin, 1e-6)
        dpg.add_progress_bar(
            tag=f"mod_bar_{tag}", default_value=float(normalized),
            width=width, overlay="",
            user_data=(vmin, vmax),
        )


def _make_color_picker(state: SharedState, label: str, tags: tuple,
                       width: int = 230) -> None:
    """Single Dear PyGui color_edit that writes back to three RGB params."""
    r_tag, g_tag, b_tag = tags
    cur = (
        float(getattr(state.params, r_tag, 0.0)),
        float(getattr(state.params, g_tag, 0.0)),
        float(getattr(state.params, b_tag, 0.0)),
    )
    color_tag = f"color_{r_tag[:-2]}"   # e.g. accent_r → color_accent

    def cb(_s, val):
        # val is [r, g, b, a] in 0..255
        state.update_param(r_tag, float(val[0]) / 255.0)
        state.update_param(g_tag, float(val[1]) / 255.0)
        state.update_param(b_tag, float(val[2]) / 255.0)

    dpg.add_color_edit(
        label=label, tag=color_tag,
        default_value=[int(cur[0] * 255), int(cur[1] * 255),
                       int(cur[2] * 255), 255],
        callback=cb,
        no_inputs=True, no_alpha=True, no_label=False,
        width=width,
    )


def _make_text_input(state: SharedState, label: str, tag: str,
                     width: int = 230) -> None:
    cur = str(getattr(state.params, tag, ""))

    def cb(_s, val):
        state.update_param(tag, str(val))

    dpg.add_input_text(
        label=label, tag=f"txt_{tag}",
        default_value=cur, callback=cb, width=width,
        on_enter=False,
    )


def _open_mod_popup(state: SharedState, target: str,
                    vmin: float, vmax: float) -> None:
    """Resolume-Arena-style audio-route editor for one param.

    Source: audio band (bass / mid / hi / peak)
    Gain:       raw signal amp
    Threshold:  gate (below = 0)
    Curve:      response exponent (<1 sensitive, >1 squashed)
    Range:      what the parameter equals at signal=0 and signal=1
    Invert:     swap min ↔ max
    Rise/Fall:  asymmetric smoothing (attack/release)

    All changes apply live. Accept confirms & closes.
    """
    popup_tag = "mod_popup"
    if dpg.does_item_exist(popup_tag):
        dpg.delete_item(popup_tag)

    existing = state.get_mod_route(target)
    init = existing if existing else ModRoute(
        target=target, source="peak", gain=1.0, threshold=0.05, curve=1.0,
        range_min=vmin, range_max=vmax, invert=False,
        smooth_rise=0.8, smooth_fall=0.15,
    )

    def on_change(_s=None, _a=None):
        """Read all widgets and push to the route."""
        if not dpg.does_item_exist(popup_tag):
            return
        src = dpg.get_value(f"{popup_tag}_src")
        if src == "off":
            state.remove_mod_route(target)
            _refresh_mod_indicator(state, target)
            return
        r = ModRoute(
            target=target,
            source=src,
            gain=float(dpg.get_value(f"{popup_tag}_gain")),
            threshold=float(dpg.get_value(f"{popup_tag}_thr")),
            curve=float(dpg.get_value(f"{popup_tag}_curve")),
            range_min=float(dpg.get_value(f"{popup_tag}_rmin")),
            range_max=float(dpg.get_value(f"{popup_tag}_rmax")),
            invert=bool(dpg.get_value(f"{popup_tag}_inv")),
            smooth_rise=float(dpg.get_value(f"{popup_tag}_rise")),
            smooth_fall=float(dpg.get_value(f"{popup_tag}_fall")),
        )
        state.add_mod_route(r)
        _refresh_mod_indicator(state, target)

    def on_accept(_s=None, _a=None):
        on_change()
        if dpg.does_item_exist(popup_tag):
            dpg.delete_item(popup_tag)

    def on_clear(_s=None, _a=None):
        state.remove_mod_route(target)
        if dpg.does_item_exist(popup_tag):
            dpg.delete_item(popup_tag)
        _refresh_mod_indicator(state, target)

    with dpg.window(label=f"Audio-link: {target}", tag=popup_tag,
                    modal=False, no_collapse=True,
                    width=440, height=420, pos=(200, 80)):
        dpg.add_combo(items=list(SOURCES), default_value=init.source,
                      label="Source", callback=on_change, width=220,
                      tag=f"{popup_tag}_src")

        # live source value indicator (audio band level the user picked)
        dpg.add_progress_bar(default_value=0.0, width=320, overlay="signal",
                             tag=f"{popup_tag}_signal")

        dpg.add_separator()
        dpg.add_text("SHAPE")
        dpg.add_slider_float(label="Gain", default_value=init.gain,
                             min_value=0.0, max_value=4.0,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_gain")
        dpg.add_slider_float(label="Threshold", default_value=init.threshold,
                             min_value=0.0, max_value=0.9,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_thr")
        dpg.add_slider_float(label="Curve", default_value=init.curve,
                             min_value=0.25, max_value=4.0,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_curve")

        dpg.add_separator()
        dpg.add_text("RANGE (what the parameter does)")
        dpg.add_slider_float(label="Min", default_value=init.range_min,
                             min_value=vmin, max_value=vmax,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_rmin")
        dpg.add_slider_float(label="Max", default_value=init.range_max,
                             min_value=vmin, max_value=vmax,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_rmax")
        dpg.add_checkbox(label="Invert", default_value=init.invert,
                         callback=on_change, tag=f"{popup_tag}_inv")

        dpg.add_separator()
        dpg.add_text("SMOOTHING")
        dpg.add_slider_float(label="Rise (attack)", default_value=init.smooth_rise,
                             min_value=0.01, max_value=1.0,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_rise")
        dpg.add_slider_float(label="Fall (release)", default_value=init.smooth_fall,
                             min_value=0.01, max_value=1.0,
                             callback=on_change, width=220,
                             tag=f"{popup_tag}_fall")

        dpg.add_separator()
        with dpg.group(horizontal=True):
            ok = dpg.add_button(label="✓ Accept", callback=on_accept, width=110)
            dpg.add_button(label="✕ Clear", callback=on_clear, width=90)
        try:
            with dpg.theme() as ok_theme:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 90, 25))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (240, 130, 50))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 165, 80))
                    dpg.add_theme_color(dpg.mvThemeCol_Text, (15, 15, 18))
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
            dpg.bind_item_theme(ok, ok_theme)
        except Exception:
            pass

    # ensure the route exists immediately so the indicator under the slider is alive
    on_change()


def _show_quit_dialog(state: SharedState) -> None:
    """Modal asking whether to save the current state as a named preset.
    Always — regardless of choice — last_session.json is autosaved by main.py
    in the finally block, so closing doesn't lose work."""
    tag = "quit_dialog"
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)

    def on_save_quit():
        dpg.delete_item(tag)
        dpg.show_item("quit_save_dialog")

    def on_quit_no_save():
        dpg.delete_item(tag)
        dpg.stop_dearpygui()

    def on_cancel():
        dpg.delete_item(tag)

    with dpg.window(label="Quit AnalogVFX",
                    tag=tag, modal=True, no_close=True, no_collapse=True,
                    width=420, height=190, pos=(60, 200)):
        dpg.add_text("Сохранить текущее состояние как именованный пресет?")
        dpg.add_text("last_session.json сохранится автоматически в любом случае —",
                     color=(160, 150, 135))
        dpg.add_text("при следующем запуске интерфейс будет такой же.",
                     color=(160, 150, 135))
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="✓ Save as preset & quit",
                           callback=lambda _s, _a: on_save_quit(), width=180)
            dpg.add_button(label="Quit (skip)",
                           callback=lambda _s, _a: on_quit_no_save(), width=110)
            dpg.add_button(label="Cancel",
                           callback=lambda _s, _a: on_cancel(), width=82)


def _refresh_mod_indicator(state: SharedState, target: str) -> None:
    """Tint the ~ button so it's obvious when a route is active."""
    btn_tag = f"mod_btn_{target}"
    if not dpg.does_item_exist(btn_tag):
        return
    active = state.get_mod_route(target) is not None
    # cheap visual: change label to show route is on
    dpg.configure_item(btn_tag, label="●" if active else "~")


def _build_pipeline_panel(state: SharedState) -> None:
    """Build/rebuild the per-effect collapsible rows."""
    if dpg.does_item_exist("pipeline_rows"):
        for child in dpg.get_item_children("pipeline_rows", 1) or []:
            try:
                dpg.delete_item(child)
            except Exception:
                pass

    # Also nuke any orphaned per-param widgets that lived inside the previous
    # build — DPG normally cascades delete, but if a tag got re-created we
    # need to remove it explicitly to avoid "alias already in use" errors.
    params_snap = state.snapshot_params()
    for cfg in params_snap.effects:
        mix_target = f"effect_mix_{cfg.kind}"
        for prefix in ("mod_btn_", "mod_bar_"):
            t = prefix + mix_target
            if dpg.does_item_exist(t):
                try:
                    dpg.delete_item(t)
                except Exception:
                    pass

    params = state.snapshot_params()
    n = len(params.effects)

    for i, cfg in enumerate(params.effects):
        kind = cfg.kind
        label = EFFECT_LABELS.get(kind, kind)
        expand_tag = f"params_{i}"
        chev_tag = f"chev_{i}"
        row_tag = f"pipeline_row_{i}"

        def make_enabled_cb(idx):
            def cb(_s, val):
                state.update_effect(idx, enabled=bool(val))
            return cb

        def make_mix_cb(idx):
            def cb(_s, val):
                state.update_effect(idx, mix=float(val))
            return cb

        def make_move_cb(idx, delta):
            def cb(_s, _a):
                state.move_effect(idx, delta)
                _build_pipeline_panel(state)
            return cb

        def make_toggle_cb(expand, chev):
            def cb(_s, _a):
                shown = dpg.get_item_configuration(expand)["show"]
                dpg.configure_item(expand, show=not shown)
                dpg.configure_item(chev, label="▼" if not shown else "▶")
            return cb

        # virtual mod target for this effect's dry/wet
        mix_target = f"effect_mix_{kind}"

        def make_open_mix_popup(t=mix_target):
            def cb(_s, _a):
                _open_mod_popup(state, t, 0.0, 1.0)
            return cb

        def make_random_effect_cb(k=kind):
            def cb(_s, _a):
                randomize_effect(state, k, set_value_cb=_push_to_slider)
            return cb

        no_mix = kind in EFFECTS_NO_MIX

        with dpg.group(parent="pipeline_rows", tag=row_tag):
            with dpg.group(horizontal=True):
                dpg.add_button(label="▶", tag=chev_tag, width=22,
                               callback=make_toggle_cb(expand_tag, chev_tag))
                dpg.add_checkbox(default_value=cfg.enabled,
                                 callback=make_enabled_cb(i))
                dpg.add_text(label, color=(255, 200, 130))
                dpg.add_spacer(width=4)
                if not no_mix:
                    dpg.add_slider_float(
                        default_value=cfg.mix, min_value=0.0, max_value=1.0,
                        callback=make_mix_cb(i), width=100,
                        format="mix %.2f", tag=f"row_mix_{i}",
                    )
                    # ~ button to route audio into the mix slider
                    mix_btn = dpg.add_button(label="~", width=22,
                                              tag=f"mod_btn_{mix_target}",
                                              callback=make_open_mix_popup())
                else:
                    # Effects in EFFECTS_NO_MIX show "ON" text instead of slider
                    dpg.add_text("[ON]", color=(120, 220, 140))
                    dpg.add_spacer(width=120)
                # 🎲 randomize only this effect's params
                dpg.add_button(label="R", width=22,
                               callback=make_random_effect_cb())
                dpg.add_button(label="^", callback=make_move_cb(i, -1),
                               enabled=(i > 0), width=24)
                dpg.add_button(label="v", callback=make_move_cb(i, +1),
                               enabled=(i < n - 1), width=24)
            # tiny live-mix bar showing the modulated mix value
            if not no_mix:
                dpg.add_progress_bar(
                    tag=f"mod_bar_{mix_target}", default_value=cfg.mix,
                    width=160, overlay="",
                    user_data=(0.0, 1.0),
                )

            with dpg.group(tag=expand_tag, show=False, indent=24):
                for plabel, ptag, vmin, vmax in EFFECT_PARAMS.get(kind, []):
                    _make_param_widget(state, plabel, ptag, vmin, vmax, width=200)
