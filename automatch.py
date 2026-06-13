"""Auto-edit: takes an audio track + a folder of media (images/videos) and
renders a 13-second beat-synced montage with beat-flash effect automation.

Re-uses the existing shader stack from the live tool — runs in headless
moderngl, swaps in media frames where camera frames used to come from.

Entry point:

    automatch.run(audio_path, media_folder, output_path, settings)

`settings` is an AutoEditSettings dataclass.
"""
from __future__ import annotations

import math
import random
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import moderngl
import numpy as np

from ascii_atlas import GLYPHS as ASCII_DEFAULT, make_atlas_array
from automatch_mod import ModMatrix
from smart_random import smart_random
from state import EffectConfig, SharedState

SHADER_DIR = Path(__file__).parent / "shaders"


# ---- vibe word lists for text cards ------------------------------------
VIBE_TEXTS: dict = {
    "cyberpunk": [
        "SECTION 9", "GHOST PROTOCOL", "NEURAL LINK",
        "01001000", "PROXY BREACH", "SCANNING…",
        "UPLINK", "NO SIGNAL", "OVERRIDE",
        "TRACE COMPLETE", "SYS://ERROR", "ACCESS DENIED",
        "GHOST IN THE NET", "MEMORY LEAK", "FRAGMENT",
    ],
    "dreamcore": [
        "you were here", "soft static", "remember when",
        "is this real", "almost", "3:47 AM",
        "somewhere else", "saudade", "bliss",
        "not yet", "again", "like before",
    ],
    "vaporwave": [
        "ＡＥＳＴＨＥＴＩＣ", "１９８４", "ＤＲＥＡＭ",
        "ＯＵＴＲＵＮ", "ＮＯＳＴＡＬＧＩＡ", "ＷＡＶＥ",
        "ＦＭ ８６", "∞",
    ],
    "noir": [
        "no witnesses", "follow the money", "end of the line",
        "she knew too much", "city never sleeps", "case closed",
        "3 AM", "rain doesn't stop", "nobody asked",
    ],
    "glitch": [
        "ERR_UNKNOWN", "0xDEADBEEF", "FATAL",
        "SIGNAL LOST", "////", "NULL",
        "░░░", "▓▓▓", "###",
    ],
    "gits": [
        "GHOST DETECTED", "BARRIER ACTIVE", "SECTION 9",
        "PUPPET MASTER", "GHOST LINE", "STAND ALONE COMPLEX",
        "LAUGHING MAN", "PROTOCOL", "HEAT HAZE",
    ],
    "urban": [
        "2:19 AM", "platform 7", "somewhere in transit",
        "not arriving", "last train", "keep moving",
    ],
    "abstract": [
        "◆", "▸ ▸ ▸", "• • •",
        "∅", "∞", "◈",
        "▓", "░ ░ ░", "///",
    ],
}


@dataclass
class TextCardSettings:
    """Controls for brief text flashes inserted at the start of some cuts."""
    enabled:    bool  = False
    probability: float = 0.40
    duration:   float = 0.80
    vibe:       str   = ""
    lines:      list  = field(default_factory=list)
    color:      tuple = (255, 255, 255)
    bg_color:   tuple = (0,   0,   0)
    bg_alpha:   float = 1.0
    font_scale: float = 1.0
    align:      str   = "center"
    font_path:  str   = ""   # explicit .ttf/.otf; "" → built-in candidate chain

    def all_texts(self) -> list:
        pool = VIBE_TEXTS.get(self.vibe, []) + list(self.lines)
        return pool or ["•"]


@dataclass
class BlobTrackSettings:
    """HSV blob detector — finds coloured regions and draws GitS-style brackets.

    `tracker_mode` selects how blobs are followed between frames:
      "hsv"   — original behaviour: per-frame HSV thresholding + connected
                components. No memory; IDs may shuffle between frames.
      "csrt"  — OpenCV CSRT tracker.  Slowest but most accurate; survives
                partial occlusion and scale change.  Re-seeded from HSV on
                init / loss.
      "kcf"   — OpenCV KCF tracker.  Fast, OK accuracy.
      "mosse" — OpenCV MOSSE tracker.  Very fast, lowest accuracy.
    """
    enabled:          bool  = False
    tracker_mode:     str   = "hsv"
    hue_center:       int   = 120
    hue_range:        int   = 30
    min_val:          int   = 80
    min_sat:          int   = 50
    min_size:         int   = 800
    max_blobs:        int   = 3
    show_overlay:     bool  = True
    bracket_color:    tuple = (80, 255, 140)
    bracket_thickness: int  = 2
    show_labels:      bool  = True
    show_centroid:    bool  = True
    show_data_line:   bool  = True
    zoom_to_blob:     bool  = False
    # ── visual extras (all opt-in; defaults reproduce the classic look) ──
    bracket_style:    int   = 0      # 0=corners 1=rectangle 2=reticle 3=dashed
    show_crosshair:   bool  = False  # full-frame crosshair through each centroid
    show_leader:      bool  = False  # diagonal leader line from bracket to label
    show_trails:      bool  = False  # fading motion trail along blob path
    trail_length:     int   = 12     # trail history length (frames)
    show_network:     bool  = False  # connect tracked blobs with lines
    label_prefix:     str   = "TGT"  # custom label prefix (was hard-coded "TGT")
    show_extra_data:  bool  = False  # append V / ID / LOCK to the data line
    show_timestamp:   bool  = False  # HUD timestamp overlay in a screen corner
    timestamp_format: int   = 0      # 0=date+time 1=time 2=date 3=frame counter


@dataclass
class DatamoshRenderSettings:
    """Real codec-level datamosh applied as an EXPORT post-process (the
    Avidemux-style bitstream technique), distinct from the live GPU `datamosh`
    effect. See datamosh_bitstream.py. Export-only — invisible in preview.

      bloom  — remove I-frames so cuts melt into each other.
      smear  — duplicate P-frames so motion stutters/freezes.
    """
    enabled:      bool  = False
    bloom:        bool  = True    # drop I-frames → melt
    smear:        bool  = False   # duplicate P-frames → stutter
    keyframe_sec: float = 2.0     # forced I-frame spacing (lower = more melts)
    bloom_chance: float = 1.0     # fraction of I-frames removed (after the 1st)
    smear_repeat: int   = 1       # extra copies per chosen P-frame (1 = smooth)
    smear_chance: float = 0.25    # fraction of P-frames duplicated
    # Finer blocks: encode the intermediate at this resolution multiplier so
    # the codec's 16×16 macroblocks cover a smaller part of the frame → the
    # melt looks finer / less blocky. 1.0 = off (fastest); 2.0 = ~half-size
    # blocks (4× the intermediate pixels). Scaled back to output on the way out.
    fine_scale:   float = 2.0


# ---------------------------------------------------------------- settings ----

@dataclass
class AutoEditSettings:
    duration: float = 13.0
    fps: int = 30
    output_w: int = 1280
    output_h: int = 720
    cuts_per_beat: float = 1.0
    beat_flash_ms: int = 200
    smart_random_each_cut: bool = True
    audio_offset_sec: float = 0.0
    crf: int = 20
    ffmpeg_preset: str = "medium"
    palette: object = "auto"
    # How strongly the palette affects the image (0=keep original colors,
    # 1=full duotone re-grade). Default 0.30 is a gentle tint, not an acid trip.
    palette_strength: float = 0.30
    # Length of the soft transition at the start of every cut (ms).
    crossfade_ms: int = 300
    # Clip-to-clip transition style — one of TRANSITION_MODES:
    #   "crossfade"        — plain dissolve (the "normal" cut)
    #   "pixel_sort_sweep" — pixel_sort surges on the cut, РАЗМЕР ramps over arc
    #   "datamosh_freeze"  — motion-vector ghosting hold
    #   "rgb_split_glitch" — RGB channels tear apart then snap back
    #   "vhs_burst"        — VHS tracking-noise burst across the cut
    #   "random"           — engine rolls one per cut (energy/phrase-weighted)
    transition_mode: str = "random"
    # Fixed seed for ALL random choices (timeline, preset, smart_random).
    # 0 = pick a fresh seed each call (preview will differ from render);
    # any non-zero value means preview and render are identical.
    seed: int = 0
    # Clip selection mode (see _pick_clip_sequence for full algorithm):
    #   "bag"     — bag/shuffle: each clip used once per pass, reshuffled on
    #               each pass, no immediate repeat across pass boundaries.
    #               DEFAULT — gives the most even distribution.
    #   "shuffle" — one permutation, then cycle through it (legacy).
    #   "random"  — independent random.choice per cut, anti-immediate-repeat.
    #   "stride"  — walk with a fixed stride (clip_stride), great for
    #               alternating two/three groups of files.
    #   "reverse" — bag mode but reverse-sorted (end-of-folder first).
    clip_mode: str = "bag"
    # Stride for "stride" mode (how many clips to skip between picks).
    clip_stride: int = 2
    # For "bag" and "random": don't pick a clip if it was used in the last
    # N cuts.  0 disables this constraint (only the immediate predecessor
    # is avoided).  Clamped to n_clips - 1 internally.
    clip_avoid_recent: int = 2
    # Preview resolution scale (1.0 = full output_w/h; 0.5 = half — much
    # faster, the live tool already runs at half).  Lower this if preview
    # stutters; render is unaffected.
    preview_scale: float = 0.5
    # Frame aspect preset.  When set to anything other than "custom" the
    # output_w/output_h fields are overridden by the resolved preset.
    #   "horizontal"  → 1280×720 (16:9)
    #   "vertical"    → 720×1280 (9:16)
    #   "square"      → 1080×1080 (1:1)
    #   "chaos"       → 1080×1080 frame, but each cut gets a virtual aspect
    #                   ratio drawn from a pool (9:16, 16:9, 1:1, 4:3, 21:9)
    #                   and is letterboxed inside the square → the "size
    #                   keeps moving" look the user asked for.
    #   "custom"      → use whatever the W/H sliders are set to
    aspect_mode: str = "horizontal"
    # Beat-sync time remap: 0=off, 0.3=subtle pulse, 0.7=dramatic.
    # Speeds up clip playback right on the beat, slows it between beats.
    beat_remap_strength: float = 0.0
    # Manual BPM override.  0 = auto-detect via librosa (HPSS+beat_track),
    # any non-zero value forces uniform beats at that tempo.  Useful when
    # auto-detection picks half/double tempo on tricky tracks (a common
    # failure mode of beat trackers).
    override_bpm: float = 0.0
    # Optical-flow camera stabilisation on input video clips.
    # Pre-computes a smoothed motion trajectory and applies the inverse warp.
    cam_stabilize: bool = False
    # When the user picks a single video file as the "media" source instead
    # of a folder, MediaPool synthesises this many virtual clips by sampling
    # random non-overlapping start offsets across the file.  Each cut then
    # plays continuously from its own offset → multi-cut edit from one
    # source video.
    single_video_clips: int = 8
    # Randomise the in-point of each clip per cut so the same source isn't
    # always seen from frame 0. Constrained to the clip's actual length so
    # the playhead doesn't wrap mid-cut.
    random_clip_starts: bool = True
    # Dither effect: explicit per-param overrides that get applied AFTER
    # smart_random so the user's chosen oscillator / colour / level settings
    # are preserved across all cuts. Keys are state.Params field names like
    # "dither_levels", "dither_color_a_r" etc.
    dither_params: dict = field(default_factory=dict)
    # ASCII mosaic: user-chosen glyph string (empty = use built-in default).
    # The atlas is re-baked inside OffscreenRenderer when this changes.
    # Order matters: light → dense (left → right).
    ascii_text: str = ""
    # Per-param ASCII overrides applied after smart_random, same pattern as
    # dither_params.  Keys: state.Params field names (ascii_cell_size,
    # ascii_invert, ascii_color_source, ascii_tint_r/g/b).
    ascii_params: dict = field(default_factory=dict)
    # Pixel sort overrides — primarily for selecting an algorithm.
    # Algorithms 0..3 are DV25 codec damage simulations; 4 (horizontal)
    # and 5 (vertical) are classic Kim-Asendorf-style sorts. Keys are
    # state.Params field names (psort_algorithm, psort_threshold,
    # psort_length, psort_color_mode).
    psort_params: dict = field(default_factory=dict)
    # Kaleidoscope overrides. Keys are state.Params field names
    # (kaleido_sides, kaleido_angle, kaleido_spin, kaleido_zoom).
    kaleido_params: dict = field(default_factory=dict)
    # Slit-scan overrides. Keys are state.Params field names
    # (slit_direction, slit_speed, slit_scanpos, slit_feedback).
    slit_params: dict = field(default_factory=dict)
    # Distort overrides. Keys are state.Params field names
    # (distort_xshift/yshift/threshold/scale/speed/blur/grain/gamma/black/white).
    distort_params: dict = field(default_factory=dict)
    # ── New-effect overrides (same field-name → value pattern as above) ──
    # Thermal (thermal_palette/gain/gamma/levels).
    thermal_params: dict = field(default_factory=dict)
    # Cellular automata (ca_cell/rate/seed_thr/mode/tint_r/tint_g/tint_b).
    cellular_params: dict = field(default_factory=dict)
    # Elastic grid (eg_grid/strength/speed/jitter).
    elastic_grid_params: dict = field(default_factory=dict)
    # CRT monitor v2 (crtm_curvature/scanline/grille/vignette/bloom/aberration).
    crt_monitor_params: dict = field(default_factory=dict)
    # Glitch block (glitch_block_size/drop_chance/repeat_chance).
    glitch_params: dict = field(default_factory=dict)
    # Modulation (mod_mode/freq/amount/speed/axis).
    modulate_params: dict = field(default_factory=dict)
    # Pixelate feedback (pixelate_block/feedback/zoom/drift/fb_mode).
    pixelate_params: dict = field(default_factory=dict)
    # Flat {Params_field: float} for the side panels wired via the generic
    # _EXTRA_FX_WIDGETS registry (vhs, crt, wave_warp, datamosh, motion_trails,
    # rgb_split, duotone, luma_key, edges, pixel_swap + all 7 new effects).
    extra_fx_params: dict = field(default_factory=dict)
    # Per-frame "human" modulation matrix (LFOs + audio envelopes → params).
    # JSON-serialisable {"lfos": [...], "routes": [...]}; see automatch_mod.py.
    # Empty dict = inert (default render unchanged).
    mod_config: dict = field(default_factory=dict)
    # Per-effect overrides — gives the user full control over what
    # _enable_beat_intensive_preset rolls. Empty dict = full auto. Keys are
    # effect kinds from USER_EFFECTS; each value is:
    #   {"state": "auto"|"on"|"off"|"random",
    #    "mix":    float in 0..1,        # honored when state in ("on", "random")
    #                                     # AND random_mix is False
    #    "random_mix": bool}             # if True, mix is rolled per cut
    # "auto"   — leave whatever the preset+smart_random rolled
    # "on"     — force enabled, use mix (or random mix)
    # "off"    — force disabled
    # "random" — per-cut 50/50 enabled, mix follows the same rule
    effect_overrides: dict = field(default_factory=dict)
    # When True: skip beat-sync cutting, apply effects to the full source as
    # one continuous clip (no timeline building, just effect pass over raw footage).
    bypass_montage: bool = False
    # Custom effect chain order — list of effect kind strings.
    # Empty list = use default USER_EFFECTS order.
    # When non-empty, snap.effects is reordered to match before each render.
    effect_chain: list = field(default_factory=list)
    # Brief text cards shown at the start of some cuts.
    text_cards:  TextCardSettings  = field(default_factory=TextCardSettings)
    blob_track:  BlobTrackSettings = field(default_factory=BlobTrackSettings)
    # Easter egg: flash the "shi o kiku" ASCII-art logo on the first beat,
    # over both preview and final render. Toggled from the GUI by clicking the
    # header title and typing "[shi o kiku]". Default on.
    logo_flash:  bool = True
    # Real codec-level datamosh post-process (Avidemux-style). Export-only;
    # the live GPU `datamosh` effect is unrelated and stays as-is.
    datamosh_render: DatamoshRenderSettings = field(default_factory=DatamoshRenderSettings)


# ─── Aspect-ratio presets ─────────────────────────────────────────────────
ASPECT_MODES = ("horizontal", "vertical", "square", "chaos", "custom")

# (w, h) per preset — used to override AutoEditSettings.output_w/h.
ASPECT_PRESETS = {
    "horizontal": (1280, 720),
    "vertical":   (720, 1280),
    "square":     (1080, 1080),
    "chaos":      (1080, 1080),
}

# Virtual aspects sampled per cut in chaos mode (label → (w, h) ratio).
CHAOS_ASPECTS = (
    ("16:9", 16.0, 9.0),
    ("9:16",  9.0, 16.0),
    ("1:1",   1.0, 1.0),
    ("4:3",   4.0, 3.0),
    ("21:9", 21.0, 9.0),
    ("3:4",   3.0, 4.0),
)


def resolve_aspect(settings) -> tuple:
    """Return the (w, h) the renderer should use given the aspect_mode."""
    mode = getattr(settings, "aspect_mode", "custom")
    if mode in ASPECT_PRESETS:
        return ASPECT_PRESETS[mode]
    return (settings.output_w, settings.output_h)


def _chaos_scale_xy(t: float) -> tuple:
    """Time-driven non-uniform X/Y scale envelope for Chaos aspect mode.

    Returns (sx, sy) ∈ approx [0.30, 1.35]² where 1.0 means "fill canvas".
    Three superimposed sinusoids per axis with prime-ish frequencies
    produce a slowly evolving organic pulse that never repeats exactly:
        sx(t) = 0.78 + 0.16·sin(1.3t) + 0.10·sin(2.1t+0.7) + 0.08·sin(0.5t+1.9)
        sy(t) = same shape, different phases + slightly different freqs

    At t=0 the values are ≈ 0.85 (slightly smaller than full so the first
    breath-in feels intentional, not a "default size"). The scales drift
    smoothly at 60 fps because they're pure continuous functions of t.
    """
    sx = (0.78
          + 0.16 * math.sin(t * 1.30)
          + 0.10 * math.sin(t * 2.10 + 0.70)
          + 0.08 * math.sin(t * 0.50 + 1.90))
    sy = (0.78
          + 0.16 * math.sin(t * 1.10 + 0.50)
          + 0.10 * math.sin(t * 1.70 + 1.40)
          + 0.08 * math.sin(t * 0.60 + 2.50))
    sx = max(0.30, min(1.35, sx))
    sy = max(0.30, min(1.35, sy))
    return sx, sy


def _apply_chaos_frame(frame: "np.ndarray", t: float,
                       target_w: int, target_h: int) -> tuple:
    """Chaos aspect mode: smoothly scale frame on X and Y independently,
    centred on a black canvas.  Returns ``(out_frame, bbox_uv)`` where
    ``bbox_uv = (x0, y0, x1, y1)`` is the rectangle (in normalised UV
    coordinates, origin top-left) where the actual video content lives.
    The renderer uses this bbox in its final shader to mask the dark
    void back to black AFTER all effects have run.
    """
    sx, sy = _chaos_scale_xy(t)
    new_w = max(2, int(round(target_w * sx)))
    new_h = max(2, int(round(target_h * sy)))

    inter = cv2.INTER_AREA if (new_w * new_h < target_w * target_h) else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (new_w, new_h), interpolation=inter)

    if new_w > target_w:
        x0 = (new_w - target_w) // 2
        resized = resized[:, x0:x0 + target_w]
        new_w = target_w
    if new_h > target_h:
        y0 = (new_h - target_h) // 2
        resized = resized[y0:y0 + target_h]
        new_h = target_h

    out = np.zeros((target_h, target_w, 3), dtype=frame.dtype)
    x0 = (target_w - new_w) // 2
    y0 = (target_h - new_h) // 2
    out[y0:y0 + new_h, x0:x0 + new_w] = resized

    # ── bbox in UV [0..1] for the final-pass mask ──
    bbox = (
        x0 / float(target_w),
        y0 / float(target_h),
        (x0 + new_w) / float(target_w),
        (y0 + new_h) / float(target_h),
    )
    return out, bbox


def _apply_aspect_crop(frame: "np.ndarray", aspect_w: float, aspect_h: float,
                        target_w: int, target_h: int) -> "np.ndarray":
    """Letterbox `frame` to a virtual (aspect_w : aspect_h) inside a
    target_w × target_h canvas with black bars.  Used by chaos mode so
    each cut reads with a different effective aspect ratio without
    changing the render output size.
    """
    if aspect_w <= 0 or aspect_h <= 0:
        return frame
    src_h, src_w = frame.shape[:2]
    virt = aspect_w / aspect_h
    target = target_w / target_h

    # Step 1: crop source to virtual aspect (center crop)
    src_aspect = src_w / src_h
    if abs(src_aspect - virt) > 1e-3:
        if src_aspect > virt:
            new_w = int(round(src_h * virt))
            x0 = (src_w - new_w) // 2
            cropped = frame[:, x0:x0 + new_w]
        else:
            new_h = int(round(src_w / virt))
            y0 = (src_h - new_h) // 2
            cropped = frame[y0:y0 + new_h, :]
    else:
        cropped = frame

    # Step 2: fit cropped into target with letterboxing
    out = np.zeros((target_h, target_w, 3), dtype=frame.dtype)
    fit_aspect = cropped.shape[1] / max(1, cropped.shape[0])
    if fit_aspect > target:
        nw = target_w
        nh = max(1, int(round(target_w / fit_aspect)))
    else:
        nh = target_h
        nw = max(1, int(round(target_h * fit_aspect)))
    if nw != cropped.shape[1] or nh != cropped.shape[0]:
        cropped = cv2.resize(cropped, (nw, nh), interpolation=cv2.INTER_AREA)
    x0 = (target_w - nw) // 2
    y0 = (target_h - nh) // 2
    out[y0:y0 + nh, x0:x0 + nw] = cropped
    return out


# Effect kinds the user can actually control via the AutoMatch GUI.
# These are the ones OffscreenRenderer can render (EFFECT_SHADERS minus the
# internal "final" pass).
USER_EFFECTS: tuple = (
    "luma_key", "duotone", "motion_trails",
    "wave_warp", "pixel_sort", "datamosh", "vhs", "ascii_mosaic",
    "dither", "rgb_split", "edges", "pixel_swap", "kaleidoscope", "slit_scan",
    "distort", "thermal", "cellular", "elastic_grid", "crt_monitor",
    "found_footage", "chromatic_aberration", "color_posterize", "swirl",
    "iridescent_foil", "motion_blur", "advanced_glitch",
)

# Effects whose wet/dry mix slider is hidden — always render at full strength.
USER_EFFECTS_NO_MIX = {"dither"}

EFFECT_LABELS_RU: dict = {
    "luma_key":     "Силуэт (luma key)",
    "duotone":      "Дуотон (monotone)",
    "dual_tone":    "Палитра (dual tone)",
    "heavy_grain":  "Зерно (grain)",
    "motion_trails":"Шлейфы (motion trails)",
    "wave_warp":    "Волны (wave warp)",
    "pixel_sort":   "Pixel sort",
    "datamosh":     "Datamosh",
    "vhs":          "VHS",
    "ascii_mosaic": "ASCII мозаика",
    "dither":       "Dither (circuit mod.)",
    "rgb_split":    "RGB split (хром. аберрация)",
    "edges":        "Контуры (edges)",
    "pixel_swap":   "Pixel swap (обмен пикселей)",
    "kaleidoscope": "Калейдоскоп (kaleidoscope)",
    "slit_scan":    "Slit-scan (врем. смаз)",
    "distort":      "Distort (карта смещения)",
    "thermal":      "Thermal (тепловизор)",
    "cellular":     "Cellular automata",
    "elastic_grid": "Elastic grid (mesh warp)",
    "crt_monitor":  "CRT",
    "glitch":                "Glitch (блочный)",
    "modulate":              "Modulation (сигнал)",
    "pixelate":              "Pixelate (фидбек)",
    "found_footage":         "Found Footage (деградация)",
    "chromatic_aberration":  "Хром. аберрация (lens)",
    "color_posterize":       "Постеризация (палитра)",
    "swirl":                 "Вихрь (swirl)",
    "iridescent_foil":       "Голографическая фольга",
    "motion_blur":           "Смаз движения (motion blur)",
    "advanced_glitch":       "Advanced Glitch (горизонт.)",
}


# Built-in palettes — (r, g, b) floats 0..1 for shadow / mid / highlight.
PALETTES: dict = {
    "auto":       None,
    "cyber_noir": {"shadow": (0.02, 0.20, 0.55),
                   "mid":    (0.55, 0.25, 0.08),
                   "highlight": (1.00, 0.65, 0.20)},
    "dreamcore":  {"shadow": (0.45, 0.30, 0.65),
                   "mid":    (0.95, 0.55, 0.85),
                   "highlight": (1.00, 0.90, 0.80)},
    "gits":       {"shadow": (0.00, 0.20, 0.30),
                   "mid":    (0.10, 0.70, 0.40),
                   "highlight": (1.00, 0.80, 0.25)},
    "warm":       {"shadow": (0.25, 0.05, 0.00),
                   "mid":    (0.85, 0.30, 0.10),
                   "highlight": (1.00, 0.85, 0.40)},
    "cold":       {"shadow": (0.00, 0.10, 0.30),
                   "mid":    (0.10, 0.45, 0.75),
                   "highlight": (0.80, 0.95, 1.00)},
    "mono":       {"shadow": (0.05, 0.05, 0.05),
                   "mid":    (0.50, 0.50, 0.50),
                   "highlight": (0.95, 0.95, 0.95)},
    "infrared":   {"shadow": (0.05, 0.00, 0.15),
                   "mid":    (1.00, 0.15, 0.30),
                   "highlight": (1.00, 0.90, 0.60)},
    "vapor":      {"shadow": (0.20, 0.10, 0.45),
                   "mid":    (1.00, 0.40, 0.70),
                   "highlight": (0.50, 0.95, 0.95)},
}


def _resolve_palette(palette) -> Optional[dict]:
    if palette is None:
        return None
    if isinstance(palette, dict) and "shadow" in palette:
        return palette
    if isinstance(palette, str):
        return PALETTES.get(palette)
    return None


def apply_palette(state: SharedState, palette, strength: float = 0.30) -> None:
    """Set dual_tone stops to the palette, but with `strength` controlling
    how strongly the duotone overrides the source. Motion-trails and bloom
    tint are NOT pushed to palette anymore — that's what made the result
    look like a Premiere preset pack. They keep neutral tint (1, 1, 1)."""
    pal = _resolve_palette(palette)
    if pal is None:
        return
    sR, sG, sB = pal["shadow"]
    mR, mG, mB = pal["mid"]
    hR, hG, hB = pal["highlight"]
    # dual_tone stops are set, but its overall strength is what makes the
    # tritone visible — clamp it to a tasteful range.
    for k, v in [("dual_shadow_r", sR), ("dual_shadow_g", sG), ("dual_shadow_b", sB),
                 ("dual_mid_r",    mR), ("dual_mid_g",    mG), ("dual_mid_b",    mB),
                 ("dual_high_r",   hR), ("dual_high_g",   hG), ("dual_high_b",   hB)]:
        state.update_param(k, float(v))
    state.update_param("dual_strength", float(max(0.0, min(1.0, strength))))
    # ASCII keeps its own bold tint (user picked the look intentionally if
    # ascii_mosaic is enabled). Other effects stay neutral so the source's
    # natural colour is preserved.
    for k, v in [("ascii_tint_r", hR), ("ascii_tint_g", hG), ("ascii_tint_b", hB)]:
        state.update_param(k, float(v))
    # neutralize motion-trails and bloom tints
    for k in ("trails_tint_r", "trails_tint_g", "trails_tint_b",
              "bloom_tint_r",  "bloom_tint_g",  "bloom_tint_b"):
        state.update_param(k, 1.0)


# ---------------------------------------------------------------- audio ----

@dataclass
class AudioAnalysis:
    """All the per-frame features the timeline-builder needs to make
    musically-aware decisions.  Fields:
        bpm        — estimated beats per minute
        beats      — beat times (seconds)
        sr         — sample rate of the loaded waveform
        onset_env  — onset strength envelope (frames)
        rms        — RMS loudness envelope (frames, same length as onset_env)
        downbeats  — beat times marked as downbeats (every 4th, phase-aligned)
        hop_sec    — seconds per frame in onset_env / rms (≈ 0.023 s @ 22050/512)
    """
    bpm: float
    beats: np.ndarray
    sr: int
    onset_env: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    rms: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    downbeats: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    hop_sec: float = 0.023

    def energy_at(self, t: float) -> float:
        """Onset strength at time t, normalised 0..1.  Higher = more punchy."""
        n = len(self.onset_env)
        if n == 0:
            return 0.5
        idx = int(max(0.0, t) / max(self.hop_sec, 1e-4))
        idx = max(0, min(n - 1, idx))
        peak = float(self.onset_env.max())
        return float(np.clip(self.onset_env[idx] / max(peak, 1e-3), 0.0, 1.0))

    def rms_at(self, t: float) -> float:
        """Loudness at time t, normalised 0..1."""
        n = len(self.rms)
        if n == 0:
            return 0.5
        idx = int(max(0.0, t) / max(self.hop_sec, 1e-4))
        idx = max(0, min(n - 1, idx))
        peak = float(self.rms.max())
        return float(np.clip(self.rms[idx] / max(peak, 1e-3), 0.0, 1.0))

    def is_downbeat(self, t: float, tol: float = 0.08) -> bool:
        """True if t is within `tol` seconds of any detected downbeat."""
        if len(self.downbeats) == 0:
            return False
        return float(np.abs(self.downbeats - t).min()) < tol

    def is_drop(self, t: float, window: float = 0.40, ratio: float = 1.8) -> bool:
        """Detect a sudden RMS spike — a "drop".  True if RMS in the last
        `window` seconds is at least `ratio`× the average RMS preceding it."""
        n = len(self.rms)
        if n < 8:
            return False
        i_now  = int(t / max(self.hop_sec, 1e-4))
        i_now  = max(0, min(n - 1, i_now))
        i_win  = int(window / max(self.hop_sec, 1e-4))
        if i_now < i_win:
            return False
        recent  = self.rms[max(0, i_now - i_win): i_now + 1]
        baseline = self.rms[max(0, i_now - 4 * i_win): max(1, i_now - i_win)]
        if len(baseline) == 0 or baseline.mean() < 1e-4:
            return False
        return bool(recent.max() > ratio * baseline.mean())


def analyze_audio_full(path: Path, offset: float = 0.0,
                       duration: float = 13.0,
                       override_bpm: float = 0.0) -> AudioAnalysis:
    """Full musical analysis: beats, onset strength, RMS, downbeats.

    Rekordbox-style improvements over the default librosa.beat_track:
      1. HPSS (harmonic-percussive separation) — beat tracker runs on the
         percussive component only.  Eliminates false beats from sustained
         pads / vocals which often confuse the autocorrelation step.
      2. Tight prior (start_bpm=120, tightness=100).  Without a prior the
         algorithm often locks to half/double tempo on EDM.
      3. Manual override (`override_bpm`): when non-zero, beats are
         synthesised uniformly at that tempo.  Use this when auto picks
         the wrong octave (typical 70-vs-140 confusion).
    """
    try:
        import librosa  # type: ignore
        y, sr = librosa.load(str(path), sr=22050, offset=offset, duration=duration)
        hop_len   = 512
        hop_sec   = float(hop_len) / float(sr)
        # ── 1) Percussion-isolated beat tracking ──
        try:
            y_perc = librosa.effects.percussive(y, margin=4.0)
        except Exception:
            y_perc = y
        if override_bpm > 0.0:
            # Manual mode: uniform beats at user-specified tempo.
            bpm   = float(override_bpm)
            period = 60.0 / bpm
            beats = np.arange(0.0, float(duration), period, dtype=np.float32)
        else:
            # ── 2) Beat track with tight prior ──
            bpm, beat_frames = librosa.beat.beat_track(
                y=y_perc, sr=sr, units="time",
                start_bpm=120.0, tightness=100.0)
            # librosa >= 0.10 returns tempo as a (1,) ndarray, not a scalar;
            # float() on a non-0-d array raises under numpy >= 2.0.
            bpm = float(np.asarray(bpm).reshape(-1)[0])
            beats = np.asarray(beat_frames, dtype=np.float32)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_len)
        rms = librosa.feature.rms(y=y, hop_length=hop_len)[0]

        # ── downbeat estimation ──
        # Convert beat times → onset-env frame indices, pick phase ∈ [0..3]
        # whose summed strength is maximum → assume 4/4 (true >90% of pop/edm).
        if len(beats) >= 4 and len(onset_env) > 0:
            beat_frames = np.clip(
                (beats / hop_sec).astype(int), 0, len(onset_env) - 1)
            sums = [float(onset_env[beat_frames[i::4]].sum())
                    for i in range(4)]
            phase = int(np.argmax(sums))
            downbeats = beats[phase::4].astype(np.float32)
        elif len(beats) > 0:
            downbeats = beats[::4].astype(np.float32)
        else:
            downbeats = np.zeros(0, np.float32)

        return AudioAnalysis(
            bpm=float(bpm), beats=beats, sr=int(sr),
            onset_env=onset_env.astype(np.float32),
            rms=rms.astype(np.float32),
            downbeats=downbeats,
            hop_sec=hop_sec,
        )
    except ImportError:
        print("[automatch] librosa not installed — falling back to onset detector")
        bpm, beats, sr = _onset_fallback(path, offset, duration)
    except Exception as e:
        print(f"[automatch] librosa failed ({e}); falling back to onset detector")
        bpm, beats, sr = _onset_fallback(path, offset, duration)
    # ── librosa-less fallback: still build an AudioAnalysis with synthesised
    #    onset/rms (constant) and downbeats as every 4th beat ──
    downbeats = beats[::4] if len(beats) >= 1 else np.zeros(0, np.float32)
    onset_env = np.ones(max(int(duration * 43), 1), np.float32) * 0.5
    rms       = np.ones_like(onset_env) * 0.3
    return AudioAnalysis(
        bpm=bpm, beats=beats, sr=sr,
        onset_env=onset_env, rms=rms, downbeats=downbeats,
        hop_sec=1.0 / 43.0,
    )


def analyze_audio(path: Path, offset: float = 0.0,
                  duration: float = 13.0) -> tuple:
    """Legacy 3-tuple return for any code that doesn't need the full
    AudioAnalysis (kept for backward compatibility)."""
    aa = analyze_audio_full(path, offset, duration)
    return aa.bpm, aa.beats, aa.sr


def _onset_fallback(path: Path, offset: float, duration: float) -> tuple:
    """Read raw PCM with imageio-ffmpeg (always available), bandpass 20–250 Hz,
    detect energy spikes."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe  # type: ignore
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        # last resort: assume 120 BPM
        bpm = 120.0
        beats = np.arange(0.0, duration, 60.0 / bpm, dtype=np.float32)
        return bpm, beats, 22050

    sr = 22050
    args = [
        ffmpeg, "-y", "-v", "quiet",
        "-ss", str(offset), "-t", str(duration),
        "-i", str(path),
        "-ac", "1", "-ar", str(sr),
        "-f", "f32le", "pipe:1",
    ]
    proc = subprocess.run(args, capture_output=True)
    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    if raw.size == 0:
        return 120.0, np.arange(0.0, duration, 0.5, dtype=np.float32), sr

    # simple onset: window energy → flux
    win = 512
    energies = np.array([np.abs(raw[i:i+win]).mean()
                         for i in range(0, len(raw) - win, win)],
                        dtype=np.float32)
    if energies.size == 0:
        return 120.0, np.arange(0.0, duration, 0.5, dtype=np.float32), sr
    flux = np.diff(energies, prepend=energies[0])
    flux = np.maximum(flux, 0.0)
    # threshold = mean + 1.5σ
    thr = flux.mean() + 1.5 * flux.std()
    onsets = np.where(flux > thr)[0]
    if onsets.size == 0:
        return 120.0, np.arange(0.0, duration, 0.5, dtype=np.float32), sr
    times = onsets * (win / sr)
    # estimate BPM from inter-onset median
    if times.size > 4:
        deltas = np.diff(times)
        med = float(np.median(deltas[deltas > 0]))
        bpm = float(np.clip(60.0 / max(med, 1e-3), 60.0, 200.0))
    else:
        bpm = 120.0
    return bpm, times.astype(np.float32), sr


# ---------------------------------------------------------------- media ----

def _load_gif_frames(path: Path) -> list:
    """Load an animated GIF with Pillow.

    Returns a list of (bgr_ndarray, duration_ms) tuples — one per frame.
    Transparency is composited onto a black background.
    Falls back to cv2 first-frame if Pillow is not installed.
    """
    frames: list = []
    try:
        from PIL import Image as _PILImage
        pil = _PILImage.open(str(path))
        while True:
            dur = int(pil.info.get("duration", 100))
            if dur < 10:
                dur = 100
            rgba = pil.convert("RGBA")
            bg   = _PILImage.new("RGBA", rgba.size, (0, 0, 0, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            bgr  = np.array(bg.convert("RGB"))[:, :, ::-1].copy()
            frames.append((bgr, dur))
            pil.seek(pil.tell() + 1)
    except EOFError:
        pass   # normal end of GIF stream
    except ImportError:
        # Pillow not installed — fall back to cv2 (first frame only)
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is not None:
            frames = [(img, 100)]
    except Exception:
        # file not found, corrupt, etc — return empty; MediaPool will produce black frame
        pass
    return frames


class MediaPool:
    """Lazy media reader. Provides get_frame(clip_idx, t_in_clip, w, h) → BGR ndarray.

    Supports: jpg / png / bmp / webp (static images),
              mp4 / mov / mkv / avi / webm (videos via cv2),
              gif (animated via Pillow, static via cv2 fallback).
    """

    IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
    GIF_EXT   = {".gif"}

    def __init__(self, folder: Path, stabilize: bool = False,
                 single_video_clips: int = 8,
                 single_video_seed: int = 0) -> None:
        """Accepts either a *directory* of media files OR a single video file.

        When given a single video, the pool synthesises ``single_video_clips``
        virtual segments by sampling random non-overlapping start offsets
        across the file.  Each virtual clip then plays continuously from its
        offset.  This lets the auto-editor build a multi-cut montage from
        one source video.
        """
        self.paths: List[Path] = []
        self._stabilize = stabilize
        self._stab_cache: dict = {}
        self._stab_pending: set = set()
        self._stab_lock = threading.Lock()
        self._vid_state: dict = {}
        self._durations: dict = {}
        self._cache: dict = {}
        # Single-video mode state.  When non-empty, all virtual clips share
        # the same underlying file and the i-th clip's frames are sampled
        # at  offset[i] + t_in_clip  inside the source video.
        self._single_offsets: List[float] = []
        self._single_path: Optional[Path] = None
        self._single_total_dur: float = 0.0

        path = Path(folder)
        if (path.is_file()
                and path.suffix.lower() in self.VIDEO_EXT
                and single_video_clips >= 1):
            # ── single-video mode ──
            self._single_path = path
            self._single_total_dur = self._probe_video_duration(path)
            if self._single_total_dur < 0.5:
                raise RuntimeError(
                    f"Single-video mode requires a video ≥ 0.5s "
                    f"(got {self._single_total_dur:.2f}s for {path.name})")
            # Choose N evenly-spread random offsets.  If single_video_seed is
            # non-zero, offsets vary per call → batch variants get genuinely
            # different segments.  If zero, fall back to file-hash so a
            # single user-triggered preview is reproducible.
            base_seed = (single_video_seed
                         if single_video_seed
                         else abs(hash(str(path.resolve()))) % (2**32))
            rng = random.Random(base_seed)
            # Sample one offset per "band" of the timeline to avoid clustering
            n = max(1, int(single_video_clips))
            band = self._single_total_dur / n
            offs = []
            for i in range(n):
                lo = i * band
                hi = min((i + 1) * band, self._single_total_dur - 0.5)
                if hi <= lo:
                    offs.append(max(0.0, lo))
                else:
                    offs.append(rng.uniform(lo, hi))
            self._single_offsets = offs
            self.paths = [path] * n
        else:
            # ── folder mode (original behaviour) ──
            all_ext = self.IMAGE_EXT | self.VIDEO_EXT | self.GIF_EXT
            if path.exists() and path.is_dir():
                for p in sorted(path.iterdir()):
                    if p.suffix.lower() in all_ext:
                        self.paths.append(p)
            if not self.paths:
                raise FileNotFoundError(
                    f"No media files in {path}  (and not a video file)")

    @staticmethod
    def _probe_video_duration(p: Path) -> float:
        """Open a throw-away VideoCapture just to read fps × frame_count."""
        cap = cv2.VideoCapture(str(p))
        try:
            if not cap.isOpened():
                return 0.0
            fps   = max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0)
            count = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
            return float(count) / float(fps)
        finally:
            cap.release()

    def __len__(self) -> int:
        return len(self.paths)

    def get_duration(self, idx: int) -> float:
        """Return clip duration in seconds. Static images return float('inf')."""
        if idx in self._durations:
            return self._durations[idx]
        # Single-video mode: each virtual clip's "duration" is the slice
        # from its offset to the next offset (or end of source).
        if self._single_offsets:
            offs = self._single_offsets + [self._single_total_dur]
            dur  = max(0.5, offs[idx + 1] - offs[idx]) if idx < len(self._single_offsets) else 0.0
            self._durations[idx] = dur
            return dur
        path = self.paths[idx]
        ext  = path.suffix.lower()
        dur: float = 0.0
        if ext in self.IMAGE_EXT:
            dur = float("inf")
        elif ext in self.GIF_EXT:
            frames = _load_gif_frames(path)
            dur = sum(d for _, d in frames) / 1000.0 if frames else 0.0
        elif ext in self.VIDEO_EXT:
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                fps   = max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0)
                count = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
                dur   = count / fps
            cap.release()
        self._durations[idx] = dur
        return dur

    def all_durations(self) -> List[float]:
        """Return durations for every clip (length = len(self.paths))."""
        return [self.get_duration(i) for i in range(len(self.paths))]

    def _open(self, idx: int):
        if idx in self._cache:
            return self._cache[idx]
        path = self.paths[idx]
        ext  = path.suffix.lower()
        if ext in self.GIF_EXT:
            obj = _load_gif_frames(path)     # list[(bgr, ms)] or []
        elif ext in self.IMAGE_EXT:
            obj = cv2.imread(str(path), cv2.IMREAD_COLOR)   # ndarray
        else:
            obj = cv2.VideoCapture(str(path))               # VideoCapture
        self._cache[idx] = obj
        # kick off stabilisation pre-compute the first time a video is opened
        if self._stabilize and isinstance(obj, cv2.VideoCapture):
            self._kick_stab(idx, path)
        return obj

    # ---------------------------------------------------------------- stabiliser

    def _kick_stab(self, idx: int, path: Path) -> None:
        """Launch background stabilisation pre-compute for clip *idx*.

        Optical-flow analysis (≈480 frames, 2–5 s) runs on a daemon thread that
        reads its OWN VideoCapture, so it never contends with the playback cap
        and never stalls preview.  Until it finishes, ``_stab_cache[idx]`` is
        absent and frames pass through un-stabilised; once the corrections land,
        subsequent frames get stabilised.  Idempotent per idx.
        """
        with self._stab_lock:
            if idx in self._stab_cache or idx in self._stab_pending:
                return
            self._stab_pending.add(idx)
        threading.Thread(target=self._stab_worker,
                         args=(idx, Path(path)), daemon=True).start()

    def _stab_worker(self, idx: int, path: Path) -> None:
        corr = None
        cap  = None
        try:
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                corr = self._compute_stab_corrections(cap)
        except Exception:
            corr = None
        finally:
            if cap is not None:
                cap.release()
        with self._stab_lock:
            self._stab_cache[idx] = corr        # ndarray (ready) or None (skip)
            self._stab_pending.discard(idx)

    @staticmethod
    def _compute_stab_corrections(cap: "cv2.VideoCapture",
                                  max_frames: int = 480) -> Optional[np.ndarray]:
        """Per-frame compensating offsets (pure numpy / cv2).  Returns an (N, 3)
        float32 array of (dx, dy, da) corrections, or None if motion is too
        sparse.  Operates on the given cap only — no shared state."""
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        fps   = max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0)
        total = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
        n     = min(total, max_frames)

        ok, frame = cap.read()
        if not ok or frame is None:
            return None

        sh, sw   = frame.shape[:2]
        scale    = min(1.0, 320.0 / max(sh, sw))
        dw, dh   = max(2, int(sw * scale)), max(2, int(sh * scale))
        to_gray  = lambda f: cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY),
                                         (dw, dh))

        transforms: list = []
        prev_gray = to_gray(frame)

        for _ in range(n - 1):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            curr_gray = to_gray(frame)
            dx = dy = da = 0.0
            pts = cv2.goodFeaturesToTrack(prev_gray, 200, 0.01, 25)
            if pts is not None and len(pts) >= 4:
                pts2, st, _ = cv2.calcOpticalFlowPyrLK(
                    prev_gray, curr_gray, pts, None)
                if st is not None:
                    mask   = st.ravel() == 1
                    good_p = pts[mask]
                    good_c = pts2[mask]
                    if len(good_p) >= 4:
                        M, _ = cv2.estimateAffinePartial2D(good_p, good_c)
                        if M is not None:
                            dx = float(M[0, 2]) / scale
                            dy = float(M[1, 2]) / scale
                            da = float(math.atan2(M[1, 0], M[0, 0]))
            transforms.append([dx, dy, da])
            prev_gray = curr_gray

        if len(transforms) < 2:
            return None

        traj = np.cumsum(transforms, axis=0)          # (N, 3) cumulative path

        # smooth with a box filter (window ≈ 0.5 s)
        win    = max(3, int(fps * 0.5) | 1)           # odd window
        pad    = win // 2
        kernel = np.ones(win, dtype=np.float64) / win
        smooth = np.stack([
            np.convolve(
                np.pad(traj[:, c], (pad, pad), mode="edge"),
                kernel, mode="valid",
            )[:len(traj)]
            for c in range(3)
        ], axis=1)

        return (smooth - traj).astype(np.float32)

    def _get_stab_matrix(self, idx: int, frame_pos: int,
                          w: int, h: int) -> Optional[np.ndarray]:
        """Return a 2×3 float32 compensating affine for *frame_pos*, or None."""
        corr = self._stab_cache.get(idx)
        if corr is None:
            return None
        i           = max(0, min(len(corr) - 1, frame_pos - 1))
        dx, dy, da  = float(corr[i, 0]), float(corr[i, 1]), float(corr[i, 2])
        cos_a, sin_a = math.cos(da), math.sin(da)
        cx, cy      = w * 0.5, h * 0.5
        return np.array([
            [cos_a, -sin_a, dx + cx * (1 - cos_a) + cy * sin_a],
            [sin_a,  cos_a, dy + cy * (1 - cos_a) - cx * sin_a],
        ], dtype=np.float32)

    # ---------------------------------------------------------------- frame reader

    def get_frame(self, idx: int, t_in_clip: float, w: int, h: int) -> np.ndarray:
        # ── Single-video mode: shift t_in_clip by the per-virtual-clip
        #    offset and share one underlying VideoCapture (cached at key 0).
        if self._single_offsets:
            shifted = (self._single_offsets[idx] + t_in_clip) % max(
                0.5, self._single_total_dur)
            obj = self._open(0)
            virtual_idx = 0
            t_in_clip = shifted
            idx = 0                            # use slot 0 for the shared cap
        else:
            obj = self._open(idx)

        if isinstance(obj, list):
            # ---- animated GIF ----
            if not obj:
                frame = np.zeros((h, w, 3), dtype=np.uint8)
            elif len(obj) == 1:
                frame = obj[0][0]
            else:
                total_ms = sum(d for _, d in obj)
                t_ms     = (t_in_clip * 1000.0) % max(1, total_ms)
                frame    = obj[-1][0]           # fallback: last frame
                acc = 0.0
                for (f, d) in obj:
                    acc += d
                    if t_ms < acc:
                        frame = f
                        break

        elif isinstance(obj, np.ndarray):
            # ---- static image ----
            frame = obj

        else:
            # ---- video (cv2.VideoCapture) ----
            cap = obj
            # Per-video state, populated once per clip — querying CAP_PROP_FPS
            # / CAP_PROP_FRAME_COUNT each frame is wasteful, and so is calling
            # cap.set(POS_FRAMES, target) when the next frame is sequential.
            vs = self._vid_state.get(idx)
            if vs is None:
                vs = {
                    "fps":         max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0),
                    "count":       max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)),
                    "last_target": -1,
                    "last_frame":  None,
                }
                self._vid_state[idx] = vs

            fps    = vs["fps"]
            count  = vs["count"]
            target = int((t_in_clip * fps) % count)
            last_t = vs["last_target"]

            frame = None
            if target == last_t and vs["last_frame"] is not None:
                # exact same frame — reuse, skip decode entirely
                frame = vs["last_frame"]
            elif target == last_t + 1:
                # sequential read — no seek needed (this is the hot path)
                ok, frame = cap.read()
                if not ok or frame is None:
                    frame = None
            # Fall back to seek + read (or recover from a failed sequential)
            if frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        frame = np.zeros((h, w, 3), dtype=np.uint8)
            vs["last_target"] = target
            vs["last_frame"]  = frame

            # camera stabilisation (applied before the letterbox resize)
            if (self._stabilize
                    and idx in self._stab_cache
                    and self._stab_cache[idx] is not None):
                fh, fw = frame.shape[:2]
                M_stab = self._get_stab_matrix(idx, target, fw, fh)
                if M_stab is not None:
                    frame = cv2.warpAffine(
                        frame, M_stab, (fw, fh),
                        flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE,
                    )

        # letterbox-cover: scale to fill, centre-crop.
        # Belt-and-suspenders: force EXACT (w, h) at the end — otherwise tiny
        # float-rounding errors leave the frame 1 px short and moderngl
        # rejects the upload with "data size mismatch".
        if frame.shape[1] != w or frame.shape[0] != h:
            ih, iw = frame.shape[:2]
            scale  = max(w / iw, h / ih)
            nw     = max(w, int(round(iw * scale)))
            nh     = max(h, int(round(ih * scale)))
            frame  = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            x0 = (nw - w) // 2
            y0 = (nh - h) // 2
            frame  = frame[y0:y0 + h, x0:x0 + w]
            if frame.shape[1] != w or frame.shape[0] != h:
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        # ensure contiguous so .tobytes() / texture.write are happy
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        return frame

    def close(self) -> None:
        for v in self._cache.values():
            if hasattr(v, "release"):
                try:
                    v.release()
                except Exception:
                    pass


# ---------------------------------------------------------------- timeline ----

@dataclass
class Cut:
    t_start: float                 # seconds into output
    t_end: float
    clip_idx: int                  # which media file
    preset_seed: int               # seed for smart_random
    motion: str = "none"           # per-cut motion type (see MOTION_TYPES)
    motion_amp: float = 0.0        # 0..1 strength of the motion
    transition: str = "crossfade"  # transition INTO this cut from prev cut
    overlay: bool = False          # if True, a smaller variant of next clip is
                                   # superimposed over the current one during cut
    # Random in-offset into the source clip (seconds), so different cuts that
    # use the same media show different parts of it
    clip_in_offset: float = 0.0
    # Virtual aspect ratio for this cut (only used when settings.aspect_mode
    # is "chaos").  Renderer letterboxes the frame to this aspect within the
    # canvas size.  (0,0) = use source as-is.
    aspect_w: float = 0.0
    aspect_h: float = 0.0
    aspect_label: str = ""           # human-readable, e.g. "16:9"
    # ── musical context, filled when an AudioAnalysis is supplied ──
    # All in 0..1 unless noted.  Drive effect intensity + motion choice.
    energy: float = 0.5              # onset strength at cut start
    loudness: float = 0.5            # RMS at cut start
    is_downbeat: bool = False        # this cut sits on a downbeat
    is_phrase_start: bool = False    # every 4th downbeat = phrase boundary
    is_drop: bool = False            # detected RMS spike just before this cut
    # Optional text card shown at the beginning of this cut
    text_card: Optional[str] = None
    text_card_dur: float = 0.0


# ---------------------------------------------------------------- blob tracking ----

# OpenCV legacy multi-object tracker support (zero new deps — ships with
# opencv-contrib-python which we already require for the colour pipeline).
_OPENCV_TRACKER_MODES = ("csrt", "kcf", "mosse")


def _make_opencv_tracker(mode: str):
    """Return a fresh OpenCV legacy tracker for the given mode, or None
    if OpenCV was built without contrib."""
    legacy = getattr(cv2, "legacy", None)
    if legacy is None:
        return None
    try:
        if   mode == "csrt":  return legacy.TrackerCSRT_create()
        elif mode == "kcf":   return legacy.TrackerKCF_create()
        elif mode == "mosse": return legacy.TrackerMOSSE_create()
    except Exception:
        return None
    return None


class _BlobTrackerState:
    """Holds per-render-pass OpenCV trackers so blob IDs persist between
    frames.  Reset to empty before each render/preview cut sequence."""
    __slots__ = ("trackers", "bboxes", "mode", "trails")

    def __init__(self, mode: str = "hsv"):
        self.trackers: list = []
        self.bboxes:   list = []
        self.mode = mode
        # per-slot centroid history for motion trails / velocity (index-keyed,
        # slot = sorted-largest-first blob position). list[list[(cx, cy)]]
        self.trails: list = []


def _track_blobs(frame_bgr: np.ndarray,
                 bt: "BlobTrackSettings",
                 state: "_BlobTrackerState | None") -> list:
    """Drop-in replacement for `_detect_blobs` that honours `bt.tracker_mode`.

    For `mode == "hsv"` or when state is None → behaves exactly like
    `_detect_blobs`.  For `csrt/kcf/mosse` → maintains a persistent set of
    OpenCV trackers, seeded from HSV detection on the first frame (or
    whenever the active set drops below `bt.max_blobs`).
    """
    if state is None or bt.tracker_mode not in _OPENCV_TRACKER_MODES:
        return _detect_blobs(frame_bgr, bt)

    # tracker mode swap mid-pass → wipe state (incl. trail history)
    if state.mode != bt.tracker_mode:
        state.trackers.clear()
        state.bboxes.clear()
        state.trails.clear()
        state.mode = bt.tracker_mode

    h, w = frame_bgr.shape[:2]

    # 1) Update existing trackers
    out: list = []
    keep_trackers, keep_bboxes = [], []
    for tk in state.trackers:
        try:
            ok, bbox = tk.update(frame_bgr)
        except Exception:
            ok = False
            bbox = (0, 0, 0, 0)
        if not ok:
            continue
        x, y, bw, bh = (int(v) for v in bbox)
        if bw < 4 or bh < 4 or x < -bw or y < -bh or x > w or y > h:
            continue
        cx = max(0, min(w - 1, x + bw // 2))
        cy = max(0, min(h - 1, y + bh // 2))
        out.append((cx, cy, int(bw * bh), (x, y, bw, bh)))
        keep_trackers.append(tk)
        keep_bboxes.append((x, y, bw, bh))
    state.trackers = keep_trackers
    state.bboxes   = keep_bboxes

    # 2) Top up from HSV detection if we have fewer than max_blobs targets
    need = max(0, bt.max_blobs - len(state.trackers))
    if need > 0:
        seeds = _detect_blobs(frame_bgr, bt)
        for (cx, cy, area, (x, y, bw, bh)) in seeds:
            if need <= 0:
                break
            # skip seeds that overlap an existing tracker (centre inside bbox)
            dup = False
            for (ex, ey, ew, eh) in state.bboxes:
                if ex <= cx <= ex + ew and ey <= cy <= ey + eh:
                    dup = True
                    break
            if dup:
                continue
            tk = _make_opencv_tracker(bt.tracker_mode)
            if tk is None:
                continue
            try:
                tk.init(frame_bgr, (x, y, bw, bh))
            except Exception:
                continue
            state.trackers.append(tk)
            state.bboxes.append((x, y, bw, bh))
            out.append((cx, cy, area, (x, y, bw, bh)))
            need -= 1

    out.sort(key=lambda b: b[2], reverse=True)
    return out[:bt.max_blobs]


def _detect_blobs(frame_bgr: np.ndarray,
                  bt: BlobTrackSettings) -> list:
    """HSV blob detection. Returns list of (cx, cy, area, (x,y,w,h)) sorted
    largest-first, at the same resolution as `frame_bgr`."""
    h, w = frame_bgr.shape[:2]

    # Down-sample for speed (detection at ≤480p is enough)
    scale = min(1.0, 480.0 / max(h, w))
    if scale < 0.99:
        dw, dh = int(w * scale), int(h * scale)
        small = cv2.resize(frame_bgr, (dw, dh), interpolation=cv2.INTER_AREA)
    else:
        small, dw, dh = frame_bgr, w, h

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hc, hr = bt.hue_center % 180, max(1, bt.hue_range)
    lo1 = np.array([(hc - hr) % 180, bt.min_sat, bt.min_val])
    hi1 = np.array([hc,               255,        255])
    lo2 = np.array([hc,               bt.min_sat, bt.min_val])
    hi2 = np.array([(hc + hr) % 180,  255,        255])

    if lo1[0] <= hi1[0]:
        mask = cv2.inRange(hsv, lo1, hi1)
    else:
        # hue wraps around 0/180
        m1 = cv2.inRange(hsv,
                          np.array([lo1[0], bt.min_sat, bt.min_val]),
                          np.array([179,    255,        255]))
        m2 = cv2.inRange(hsv,
                          np.array([0,       bt.min_sat, bt.min_val]),
                          np.array([hi1[0],  255,        255]))
        mask = cv2.bitwise_or(m1, m2)
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo2, hi2))

    # morphological cleanup
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

    sx, sy = w / dw, h / dh          # back-scale factors
    min_px = bt.min_size / (sx * sy)  # size threshold in detect-res pixels

    blobs = []
    for i in range(1, n):
        area_s = int(stats[i, cv2.CC_STAT_AREA])
        if area_s < min_px:
            continue
        cx = int(centroids[i][0] * sx)
        cy = int(centroids[i][1] * sy)
        bx = int(stats[i, cv2.CC_STAT_LEFT]   * sx)
        by = int(stats[i, cv2.CC_STAT_TOP]    * sy)
        bw = int(stats[i, cv2.CC_STAT_WIDTH]  * sx)
        bh = int(stats[i, cv2.CC_STAT_HEIGHT] * sy)
        blobs.append((cx, cy, int(area_s * sx * sy), (bx, by, bw, bh)))

    blobs.sort(key=lambda b: b[2], reverse=True)
    return blobs[:bt.max_blobs]


def _dashed_line(img, p1, p2, col, thick=1, dash=8, gap=6):
    """Anti-aliased dashed segment between two integer points."""
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist < 1.0:
        return
    period = float(dash + gap)
    i = 0.0
    while i < dist:
        a = i / dist
        b = min(1.0, (i + dash) / dist)
        cv2.line(img,
                 (int(x1 + (x2 - x1) * a), int(y1 + (y2 - y1) * a)),
                 (int(x1 + (x2 - x1) * b), int(y1 + (y2 - y1) * b)),
                 col, thick, cv2.LINE_AA)
        i += period


def _dashed_rect(img, x1, y1, x2, y2, col, thick=1):
    _dashed_line(img, (x1, y1), (x2, y1), col, thick)
    _dashed_line(img, (x2, y1), (x2, y2), col, thick)
    _dashed_line(img, (x2, y2), (x1, y2), col, thick)
    _dashed_line(img, (x1, y2), (x1, y1), col, thick)


def _draw_hud_timestamp(result, bt, frame_idx, col):
    """Top-left HUD timestamp + blinking REC indicator (drawn once/frame)."""
    from datetime import datetime
    h, w = result.shape[:2]
    fmt  = int(getattr(bt, "timestamp_format", 0))
    now  = datetime.now()
    if   fmt == 1: stamp = now.strftime("%H:%M:%S")
    elif fmt == 2: stamp = now.strftime("%Y-%m-%d")
    elif fmt == 3: stamp = f"FRAME {int(frame_idx):06d}"
    else:          stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    fs     = max(0.5, min(w, h) / 900.0)
    margin = max(10, int(min(w, h) * 0.03))
    y      = margin + 6
    # blinking red REC dot (~2 Hz at 30fps)
    if (int(frame_idx) // 15) % 2 == 0:
        cv2.circle(result, (margin + 6, y - 4), 5, (0, 0, 255), -1, cv2.LINE_AA)
    cv2.putText(result, "REC", (margin + 18, y),
                cv2.FONT_HERSHEY_PLAIN, fs, col, 1, cv2.LINE_AA)
    cv2.putText(result, stamp, (margin, y + 18),
                cv2.FONT_HERSHEY_PLAIN, fs, col, 1, cv2.LINE_AA)


def _draw_blob_overlay(frame: np.ndarray,
                       blobs: list,
                       bt: BlobTrackSettings,
                       state: "_BlobTrackerState | None" = None,
                       frame_idx: int = 0) -> np.ndarray:
    """Draw GitS-style HUD marks on detected blobs.

    Supports several opt-in extras (all default-off so the classic look is
    unchanged): bracket styles, full-frame crosshair, leader lines, fading
    motion trails, an inter-blob connection network, a customisable label
    prefix, extra data (velocity / ID / lock), and a HUD timestamp.
    Renders on a copy so the shaders pick up the marks."""
    result = frame.copy()
    h, w = result.shape[:2]
    # RGB → BGR for cv2
    col = (bt.bracket_color[2], bt.bracket_color[1], bt.bracket_color[0])
    t   = max(1, bt.bracket_thickness)
    prefix = (getattr(bt, "label_prefix", "TGT") or "TGT").strip() or "TGT"
    style  = int(getattr(bt, "bracket_style", 0))

    centroids = [(cx, cy) for (cx, cy, _a, _b) in blobs]

    # ── per-slot trail history (needed for trails AND velocity) ──
    trails = None
    if state is not None and (getattr(bt, "show_trails", False)
                              or getattr(bt, "show_extra_data", False)):
        tl = max(2, int(getattr(bt, "trail_length", 12)))
        while len(state.trails) < len(centroids):
            state.trails.append([])
        if len(state.trails) > len(centroids):
            state.trails = state.trails[:len(centroids)]
        for i, c in enumerate(centroids):
            state.trails[i].append(c)
            if len(state.trails[i]) > tl:
                state.trails[i] = state.trails[i][-tl:]
        trails = state.trails

    # ── connection network between blobs ──
    if getattr(bt, "show_network", False) and len(centroids) >= 2:
        fsn = max(0.35, min(w, h) / 1200.0)
        for i in range(len(centroids)):
            for j in range(i + 1, len(centroids)):
                _dashed_line(result, centroids[i], centroids[j], col, 1, 10, 8)
                mx = (centroids[i][0] + centroids[j][0]) // 2
                my = (centroids[i][1] + centroids[j][1]) // 2
                d  = int(math.hypot(centroids[i][0] - centroids[j][0],
                                    centroids[i][1] - centroids[j][1]))
                cv2.putText(result, f"{d}", (mx, my),
                            cv2.FONT_HERSHEY_PLAIN, fsn, col, 1, cv2.LINE_AA)

    for idx, (cx, cy, area, (bx, by, bw, bh)) in enumerate(blobs):
        pad  = max(6, int(min(bw, bh) * 0.12))
        x1   = max(0, bx - pad)
        y1   = max(0, by - pad)
        x2   = min(w - 1, bx + bw + pad)
        y2   = min(h - 1, by + bh + pad)
        arm  = max(8, min(bw, bh) // 4)   # corner arm length

        # ── full-frame crosshair through centroid ──
        if getattr(bt, "show_crosshair", False):
            cv2.line(result, (0, cy), (w - 1, cy), col, 1, cv2.LINE_AA)
            cv2.line(result, (cx, 0), (cx, h - 1), col, 1, cv2.LINE_AA)

        # ── fading motion trail ──
        if trails is not None and getattr(bt, "show_trails", False) \
                and idx < len(trails) and len(trails[idx]) >= 2:
            pts = trails[idx]
            n   = len(pts)
            for k in range(1, n):
                fade = k / n
                tcol = tuple(int(c0 * (0.25 + 0.75 * fade)) for c0 in col)
                cv2.line(result, pts[k - 1], pts[k], tcol,
                         max(1, int(round(t * fade))), cv2.LINE_AA)

        # ── bracket (style-dependent) ──
        if style == 1:          # full rectangle
            cv2.rectangle(result, (x1, y1), (x2, y2), col, t, cv2.LINE_AA)
        elif style == 2:        # circle / reticle
            r = max(arm * 2, max(x2 - x1, y2 - y1) // 2)
            ccx, ccy = (x1 + x2) // 2, (y1 + y2) // 2
            cv2.circle(result, (ccx, ccy), r, col, t, cv2.LINE_AA)
            for (dx, dy) in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                cv2.line(result, (ccx + dx * r, ccy + dy * r),
                         (ccx + dx * (r + arm // 2), ccy + dy * (r + arm // 2)),
                         col, t, cv2.LINE_AA)
        elif style == 3:        # dashed rectangle
            _dashed_rect(result, x1, y1, x2, y2, col, t)
        else:                   # 0 = corner brackets (classic)
            for (px, py, dx, dy) in (
                (x1, y1, +1,  0), (x1, y1,  0, +1),   # TL
                (x2, y1, -1,  0), (x2, y1,  0, +1),   # TR
                (x1, y2, +1,  0), (x1, y2,  0, -1),   # BL
                (x2, y2, -1,  0), (x2, y2,  0, -1),   # BR
            ):
                cv2.line(result, (px, py),
                         (px + dx * arm, py + dy * arm), col, t, cv2.LINE_AA)

        # Crosshair at centroid
        if bt.show_centroid:
            cs = max(4, arm // 2)
            cv2.line(result, (cx - cs, cy), (cx + cs, cy), col, 1, cv2.LINE_AA)
            cv2.line(result, (cx, cy - cs), (cx, cy + cs), col, 1, cv2.LINE_AA)
            cv2.circle(result, (cx, cy), 2, col, -1, cv2.LINE_AA)

        # Label (with optional leader line up-left from the bracket corner)
        label  = f"{prefix}.{idx+1:02d}"
        fs     = max(0.4, min(w, h) / 800.0)
        lx, ly = x1, max(10, y1 - 6)
        if getattr(bt, "show_leader", False):
            lead_x = max(2, x1 - max(12, arm))
            lead_y = max(10, y1 - max(12, arm))
            cv2.line(result, (x1, y1), (lead_x, lead_y), col, 1, cv2.LINE_AA)
            cv2.circle(result, (lead_x, lead_y), 2, col, -1, cv2.LINE_AA)
            lx, ly = lead_x, max(10, lead_y - 4)
        if bt.show_labels:
            cv2.putText(result, label, (lx, ly),
                        cv2.FONT_HERSHEY_PLAIN, fs, col, 1, cv2.LINE_AA)

        # Data line below bracket (+ optional velocity / ID / lock)
        if bt.show_data_line:
            data = f"AREA:{area:05d}  X:{cx:04d} Y:{cy:04d}"
            if getattr(bt, "show_extra_data", False):
                v = 0
                if trails is not None and idx < len(trails) \
                        and len(trails[idx]) >= 2:
                    (px0, py0), (px1, py1) = trails[idx][-2], trails[idx][-1]
                    v = int(round(math.hypot(px1 - px0, py1 - py0)))
                # [TRACK] = OpenCV tracker is active for this slot;
                # [SCAN]  = falling back to per-frame HSV detection.
                tracking = (state is not None
                            and idx < len(state.trackers))
                data += f"  V:{v:03d} ID:{idx+1:02d}"
                data += "  [TRACK]" if tracking else "  [SCAN]"
            fs2 = max(0.35, min(w, h) / 1000.0)
            cv2.putText(result, data,
                        (x1, min(h - 4, y2 + 14)),
                        cv2.FONT_HERSHEY_PLAIN, fs2, col, 1, cv2.LINE_AA)

    # ── HUD timestamp (independent of blob count) ──
    if getattr(bt, "show_timestamp", False):
        _draw_hud_timestamp(result, bt, frame_idx, col)

    return result


def _blob_zoom_frame(frame: np.ndarray,
                     cx_n: float, cy_n: float,
                     t_in_cut: float, cut_dur: float,
                     amp: float) -> np.ndarray:
    """Gentle progressive zoom toward (cx_n, cy_n) over the cut duration."""
    h, w  = frame.shape[:2]
    prog  = min(1.0, t_in_cut / max(0.001, cut_dur))
    zoom  = 1.0 + amp * 0.4 * prog   # up to +40% of amp
    # centre of zoom
    cx    = cx_n * w
    cy    = cy_n * h
    tx    = (1 - zoom) * cx
    ty    = (1 - zoom) * cy
    M     = np.array([[zoom, 0.0, tx],
                       [0.0, zoom, ty]], dtype=np.float32)
    return cv2.warpAffine(frame, M, (w, h),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


# ------------------------------------------------------------- logo flash ----
#
# Easter egg: on the first beat we flash the "shi o kiku" ASCII-art logo over
# the frame (preview + final render). _LOGO_LAYER_CACHE holds the pre-rendered
# white-on-transparent logo bitmap keyed by (w, h) so we only rasterise once.

_LOGO_FLASH_WINDOW = 0.55   # seconds the flash is visible from the first beat
_LOGO_LAYER_CACHE: dict = {}


def _logo_flash_alpha(t: float, beats, settings) -> float:
    """Fade alpha (0..1) for the first-beat logo flash at time ``t``.

    Returns 0.0 when the flash is disabled, there are no beats, or ``t`` is
    outside the flash window. Inside the window the alpha rises fast and decays
    (quick attack, slower release) so it reads as a single percussive stamp.
    """
    if not getattr(settings, "logo_flash", False):
        return 0.0
    if beats is None or len(beats) == 0:
        return 0.0
    b0 = float(beats[0])
    dt = t - b0
    if dt < 0.0 or dt > _LOGO_FLASH_WINDOW:
        return 0.0
    x = dt / _LOGO_FLASH_WINDOW            # 0..1 across the window
    # attack over first 20%, release over the rest
    if x < 0.2:
        return x / 0.2
    return max(0.0, 1.0 - (x - 0.2) / 0.8)


def _logo_flash_layer(w: int, h: int):
    """Return a cached (mask, rgb) pair for the ASCII logo sized to (w, h).

    mask: float32 (h, w) in 0..1 — per-pixel logo coverage.
    rgb:  uint8  (h, w, 3) BGR — the logo colour (white).
    Returns None if the logo art / PIL is unavailable.
    """
    key = (w, h)
    cached = _LOGO_LAYER_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from PIL import Image, ImageDraw, ImageFont
        try:
            from logo_ascii import ASCII_LOGO
        except Exception:
            ASCII_LOGO = "shi o kiku"

        lines = ASCII_LOGO.split("\n")
        n_rows = max(1, len(lines))
        n_cols = max(1, max(len(ln) for ln in lines))

        # pick a monospace font size so the art fits ~82% of the frame
        mono_candidates = [r"C:\Windows\Fonts\consola.ttf",
                           r"C:\Windows\Fonts\cour.ttf",
                           r"C:\Windows\Fonts\lucon.ttf"]
        font_path = next((p for p in mono_candidates if Path(p).exists()), "")
        # estimate: monospace char advance ~0.6 of font size, line height ~1.0
        fit_w = (0.82 * w) / (n_cols * 0.60)
        fit_h = (0.82 * h) / (n_rows * 1.05)
        fsize = max(4, int(min(fit_w, fit_h)))
        if font_path:
            font = ImageFont.truetype(font_path, fsize)
        else:
            font = ImageFont.load_default()

        # measure the block and centre it
        tmp = Image.new("L", (8, 8), 0)
        d0  = ImageDraw.Draw(tmp)
        bbox = d0.multiline_textbbox((0, 0), ASCII_LOGO, font=font, spacing=0)
        blk_w = bbox[2] - bbox[0]
        blk_h = bbox[3] - bbox[1]
        ox = (w - blk_w) // 2 - bbox[0]
        oy = (h - blk_h) // 2 - bbox[1]

        mask_img = Image.new("L", (w, h), 0)
        dm = ImageDraw.Draw(mask_img)
        dm.multiline_text((ox, oy), ASCII_LOGO, font=font, fill=255, spacing=0)

        mask = (np.asarray(mask_img, dtype=np.float32) / 255.0)
        rgb  = np.full((h, w, 3), 255, dtype=np.uint8)   # white, BGR-agnostic
        _LOGO_LAYER_CACHE[key] = (mask, rgb)
        return _LOGO_LAYER_CACHE[key]
    except Exception:
        _LOGO_LAYER_CACHE[key] = None
        return None


def _overlay_ascii_logo(frame: np.ndarray, alpha: float) -> np.ndarray:
    """Alpha-blend the cached ASCII logo onto a BGR frame; returns BGR frame."""
    if alpha <= 0.0:
        return frame
    h, w = frame.shape[:2]
    layer = _logo_flash_layer(w, h)
    if layer is None:
        return frame
    mask, rgb = layer
    a = (mask * float(np.clip(alpha, 0.0, 1.0)))[:, :, None]   # (h, w, 1)
    out = frame.astype(np.float32) * (1.0 - a) + rgb.astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- text cards ----

# Cache rendered text cards (solid-background variant only — overlay mode
# depends on the underlying frame so it can't be cached).
# Key: (text, w, h, color, bg_color, font_scale, align). Value: BGR ndarray.
_TEXT_CARD_CACHE: dict = {}
_TEXT_CARD_CACHE_MAX = 64


def _render_text_card(
    text:       str,
    w:          int,
    h:          int,
    color:      tuple = (255, 255, 255),  # RGB
    bg_color:   tuple = (0, 0, 0),        # RGB
    bg_alpha:   float = 1.0,
    font_scale: float = 1.0,
    align:      str   = "center",
    base_frame: Optional[np.ndarray] = None,  # BGR source for overlay mode
    font_path:  str   = "",                   # explicit font; "" → default chain
) -> np.ndarray:
    """Render text onto a frame and return BGR numpy array (h, w, 3).

    bg_alpha=1 → solid background; bg_alpha=0 → text composited over base_frame.
    """
    # ── fast-path: solid-background cards are identical every frame they're
    #    shown, so cache the rendered ndarray and skip PIL/font lookup. ──
    _cached_ok = bg_alpha >= 0.99 or base_frame is None
    cache_key  = None
    if _cached_ok:
        cache_key = (text, w, h, color, bg_color,
                     round(font_scale, 3), align, font_path)
        cached = _TEXT_CARD_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        from PIL import Image, ImageDraw, ImageFont

        if bg_alpha < 0.99 and base_frame is not None:
            pil_img = Image.fromarray(base_frame[:, :, ::-1].astype(np.uint8), "RGB")
        else:
            pil_img = Image.new("RGB", (w, h), (bg_color[0], bg_color[1], bg_color[2]))

        draw      = ImageDraw.Draw(pil_img)
        font_size = max(18, int(min(w, h) * 0.07 * font_scale))
        font      = None
        candidates = ([font_path] if font_path else []) + [
                   r"C:\Windows\Fonts\consola.ttf",
                   r"C:\Windows\Fonts\consolab.ttf",
                   r"C:\Windows\Fonts\courbd.ttf",
                   r"C:\Windows\Fonts\arial.ttf",
                   r"C:\Windows\Fonts\segoeui.ttf"]
        for fp in candidates:
            if fp and Path(fp).exists():
                try:
                    font = ImageFont.truetype(fp, font_size)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        lines    = text.split("\n")
        gap      = max(4, font_size // 5)
        bboxes   = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
        lh_list  = [bb[3] - bb[1] for bb in bboxes]
        lw_list  = [bb[2] - bb[0] for bb in bboxes]
        total_th = sum(lh_list) + gap * max(0, len(lines) - 1)
        y        = (h - total_th) // 2
        text_rgba = (color[0], color[1], color[2])

        for i, line in enumerate(lines):
            lw = lw_list[i]
            if align == "center":
                x = max(0, (w - lw) // 2)
            elif align == "left":
                x = w // 12
            else:
                x = max(0, w - lw - w // 12)
            draw.text((x, y), line, font=font, fill=text_rgba)
            y += lh_list[i] + gap

        # When overlaying, blend with bg_alpha controlling transparency of bg
        if bg_alpha < 0.99 and base_frame is not None:
            text_only = Image.new("RGB", (w, h), (bg_color[0], bg_color[1], bg_color[2]))
            draw2 = ImageDraw.Draw(text_only)
            y2 = (h - total_th) // 2
            for i, line in enumerate(lines):
                lw = lw_list[i]
                if align == "center":
                    x2 = max(0, (w - lw) // 2)
                elif align == "left":
                    x2 = w // 12
                else:
                    x2 = max(0, w - lw - w // 12)
                draw2.text((x2, y2), line, font=font, fill=text_rgba)
                y2 += lh_list[i] + gap
            pil_img = Image.blend(pil_img, text_only, alpha=max(0.15, 1.0 - bg_alpha))

        out = np.array(pil_img)[:, :, ::-1].astype(np.uint8)
        if cache_key is not None:
            if len(_TEXT_CARD_CACHE) >= _TEXT_CARD_CACHE_MAX:
                _TEXT_CARD_CACHE.pop(next(iter(_TEXT_CARD_CACHE)))
            _TEXT_CARD_CACHE[cache_key] = out
        return out

    except ImportError:
        # PIL not available — plain cv2 fallback
        if bg_alpha < 0.99 and base_frame is not None:
            result = base_frame.copy()
        else:
            result = np.zeros((h, w, 3), dtype=np.uint8)
            result[:] = (bg_color[2], bg_color[1], bg_color[0])  # RGB→BGR

        cv_scale = max(0.5, 1.4 * font_scale * min(w, h) / 720)
        cv_col   = (color[2], color[1], color[0])
        lines    = text.split("\n")
        th_cv    = max(30, int(40 * font_scale))
        start_y  = (h - len(lines) * th_cv) // 2 + th_cv
        for i, line in enumerate(lines):
            (tw, _), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, cv_scale, 2)
            if align == "center":
                x = max(0, (w - tw) // 2)
            elif align == "left":
                x = w // 12
            else:
                x = max(0, w - tw - w // 12)
            cv2.putText(result, line, (x, start_y + i * th_cv),
                        cv2.FONT_HERSHEY_SIMPLEX, cv_scale, cv_col, 2, cv2.LINE_AA)
        if cache_key is not None:
            if len(_TEXT_CARD_CACHE) >= _TEXT_CARD_CACHE_MAX:
                _TEXT_CARD_CACHE.pop(next(iter(_TEXT_CARD_CACHE)))
            _TEXT_CARD_CACHE[cache_key] = result
        return result


TRANSITION_TYPES = (
    # only two used in auto-edit by default; the others stay in the file for
    # manual experimentation but are weighted at 0 in build_timeline below.
    "crossfade", "datamosh_freeze",
    "luma_wipe", "pip_zoom", "slide_left", "slide_right",
    "slide_up", "wipe_diag",
)

MOTION_TYPES = (
    "none", "zoom_in", "zoom_out", "pan_lr", "pan_ud",
    "ken_burns",
    # shake / rotate / glitch_jump are still defined in apply_motion for manual
    # use, but they read too "Premiere preset" so they're not in the auto roll.
)


CLIP_MODES = ("bag", "shuffle", "random", "stride", "reverse")


def _pick_clip_sequence(n_clips: int, n_cuts: int, mode: str,
                         stride: int, avoid_recent: int,
                         rng: random.Random) -> List[int]:
    """Generate the per-cut clip index sequence.  See AutoEditSettings.clip_mode
    for the semantics of each mode."""
    if n_clips <= 0:
        return [0] * n_cuts
    if n_clips == 1:
        return [0] * n_cuts

    # how many of the last K picks to avoid repeating (clamped)
    K = max(0, min(avoid_recent, n_clips - 1))

    if mode == "shuffle":
        # legacy: single permutation, cycled
        order = list(range(n_clips))
        rng.shuffle(order)
        return [order[i % n_clips] for i in range(n_cuts)]

    if mode == "reverse":
        # bag but starting from end of folder
        order = list(range(n_clips - 1, -1, -1))
        seq: List[int] = []
        while len(seq) < n_cuts:
            for i in order:
                if seq and i == seq[-1]:
                    continue
                seq.append(i)
                if len(seq) >= n_cuts:
                    break
        return seq

    if mode == "stride":
        # alternating walk: 0, S, 2S, ... wrap with a random starting point
        s     = max(1, min(stride, n_clips - 1))
        start = rng.randint(0, n_clips - 1)
        seq = []
        for i in range(n_cuts):
            idx = (start + i * s) % n_clips
            # if stride coprime with n_clips we already get full coverage;
            # otherwise nudge every full cycle so we don't loop the same 2-3
            if i > 0 and i % n_clips == 0:
                start = (start + 1) % n_clips
            seq.append(idx)
        return seq

    if mode == "random":
        seq = []
        for _ in range(n_cuts):
            recent = set(seq[-(K + 1):]) if K > 0 else (
                {seq[-1]} if seq else set())
            for _try in range(8):
                pick = rng.randint(0, n_clips - 1)
                if pick not in recent:
                    break
            seq.append(pick)
        return seq

    # Default: "bag" — each clip exactly once per pass, reshuffled per pass,
    # no immediate repeat across pass boundaries, plus optional avoid_recent.
    seq = []
    bag: list = []
    while len(seq) < n_cuts:
        if not bag:
            bag = list(range(n_clips))
            rng.shuffle(bag)
            # honour avoid_recent at bag boundary
            recent = set(seq[-K:]) if K > 0 else (
                {seq[-1]} if seq else set())
            if recent:
                # rotate bag so the head is NOT in recent
                tries = 0
                while bag[0] in recent and tries < n_clips:
                    bag.append(bag.pop(0))
                    tries += 1
                # if that failed (everything in recent — happens when K
                # ≥ n_clips), just take the next bag as-is
        seq.append(bag.pop(0))
    return seq


def _choose_motion(rng: random.Random, energy: float,
                   is_drop: bool, is_downbeat: bool) -> tuple:
    """Pick a motion type + amplitude based on musical context.

    High energy / drops → dynamic motion (zoom_in, shake).
    Low energy → calm (none, ken_burns).
    Amplitude scales linearly with energy.
    """
    if is_drop:
        kind = rng.choices(
            ["zoom_in", "shake", "glitch_jump"], weights=[3, 3, 2], k=1)[0]
        amp  = rng.uniform(0.35, 0.65)
    elif energy > 0.65 or is_downbeat:
        kind = rng.choices(
            ["zoom_in", "ken_burns", "pan_lr", "pan_ud", "rotate_cw"],
            weights=[4, 3, 2, 2, 1], k=1)[0]
        amp  = rng.uniform(0.20, 0.45)
    elif energy > 0.35:
        kind = rng.choices(
            ["none", "ken_burns", "zoom_in", "pan_lr"],
            weights=[3, 4, 2, 2], k=1)[0]
        amp  = rng.uniform(0.15, 0.30)
    else:
        kind = rng.choices(
            ["none", "ken_burns", "zoom_out"],
            weights=[5, 3, 1], k=1)[0]
        amp  = rng.uniform(0.10, 0.25)
    return kind, amp


# Effect-driven transitions: instead of a geometric wipe, the engine forces a
# shader effect full-on across the fade window, automating its intensity over a
# 0→1→0 arc so the effect swells on the cut.  See _apply_effect_transition().
EFFECT_TRANSITIONS = ("pixel_sort_sweep", "datamosh_freeze",
                      "rgb_split_glitch", "vhs_burst")

# Transition styles selectable from the GUI dropdown.
TRANSITION_MODES = ("crossfade",) + EFFECT_TRANSITIONS + ("random",)


def _choose_transition(rng: random.Random, energy: float,
                       is_phrase_start: bool, is_drop: bool,
                       index: int, mode: str = "random") -> str:
    """Pick a transition style for cut `index`.

    First cut → crossfade (smooth in).
    Fixed `mode` → always that style (after the first cut).
    "random"  → energy-weighted roll across crossfade + all effect transitions,
                with phrase boundaries / drops favouring the punchier ones.
    """
    if index == 0:
        return "crossfade"
    if mode != "random" and mode in TRANSITION_MODES:
        return mode
    # ── random mode: crossfade-dominant, effect transitions as accents ──
    # Tuned so effect transitions land on roughly ~30% of cuts overall — a
    # clean beat-cut montage with punctuating glitches, not a glitch wall.
    if is_phrase_start or is_drop:
        # musical accent → favour punchy effects, but a clean cut is still
        # the single most likely outcome (~50% effect on these cuts)
        return rng.choices(
            ["crossfade", "datamosh_freeze", "pixel_sort_sweep",
             "rgb_split_glitch", "vhs_burst"],
            weights=[12, 4, 3, 3, 2], k=1)[0]
    # ordinary cuts: crossfade dominates; effects scale gently with energy
    e = 0.5 + 2.0 * energy
    return rng.choices(
        ["crossfade", "datamosh_freeze", "pixel_sort_sweep",
         "rgb_split_glitch", "vhs_burst"],
        weights=[16.0, e, e * 0.8, e * 0.7, e * 0.6], k=1)[0]


def _transition_arc(fade_alpha: float) -> float:
    """0→1→0 envelope peaking at the midpoint of the transition."""
    a = max(0.0, min(1.0, fade_alpha))
    return math.sin(math.pi * a)


def _force_effect_on(params, kind: str, mix: float) -> None:
    """Enable effect `kind` in params.effects at `mix` (≥ current if already
    enabled), appending a config if the kind is missing entirely."""
    for eff in params.effects:
        if eff.kind == kind:
            base = eff.mix if eff.enabled else 0.0
            eff.enabled = True
            eff.mix = max(base, mix)
            return
    try:
        from state import EffectConfig
        params.effects.append(EffectConfig(kind=kind, enabled=True, mix=mix))
    except Exception:
        pass


def _apply_effect_transition(params, kind: str, fade_alpha: float) -> None:
    """Force the shader effect bound to transition `kind` on across the fade,
    automating its intensity over a 0→1→0 arc (peaks mid-cut)."""
    arc = _transition_arc(fade_alpha)
    if kind == "datamosh_freeze":
        _force_effect_on(params, "datamosh", 1.0)
    elif kind == "pixel_sort_sweep":
        # pixel_sort is always full-wet; automate РАЗМЕР (psort_length) on the arc.
        params.psort_length = max(getattr(params, "psort_length", 0.40),
                                  0.12 + 0.88 * arc)
        _force_effect_on(params, "pixel_sort", 1.0)
    elif kind == "rgb_split_glitch":
        params.rgb_shift_amount = max(getattr(params, "rgb_shift_amount", 0.0),
                                      0.05 + 0.60 * arc)
        _force_effect_on(params, "rgb_split", min(1.0, 0.40 + 0.60 * arc))
    elif kind == "vhs_burst":
        params.vhs_tracking_noise = max(getattr(params, "vhs_tracking_noise", 0.0),
                                        0.20 + 0.80 * arc)
        params.vhs_line_jitter = max(getattr(params, "vhs_line_jitter", 0.0),
                                     0.50 * arc)
        _force_effect_on(params, "vhs", min(1.0, 0.40 + 0.60 * arc))


def build_timeline(beats: np.ndarray, settings: AutoEditSettings,
                   n_clips: int, seed: int = 0,
                   clip_durations: Optional[List[float]] = None,
                   audio: Optional[AudioAnalysis] = None,
                   get_duration: Optional[Callable[[int], float]] = None) -> List[Cut]:
    """Pick beat-aligned cut points and assign clips + per-cut preset seeds.

    Clip selection follows `settings.clip_mode`:
      "shuffle"  — full shuffle, each clip used before any repeats
      "random"   — independent random.choice with no immediate repetition

    When `clip_durations` is given AND `settings.random_clip_starts` is True,
    each cut also receives a random in-offset into its source clip so the
    same media is never seen from the same position twice.
    """
    if beats.size < 2:
        step = settings.duration / 4
        return [Cut(i*step, (i+1)*step, i % n_clips, seed + i) for i in range(4)]

    rng = random.Random(seed)
    k = max(1, int(round(1.0 / max(settings.cuts_per_beat, 0.01))))
    cut_beats = beats[::k]
    cut_beats = cut_beats[cut_beats < settings.duration]
    if cut_beats.size == 0 or cut_beats[0] > 0.05:
        cut_beats = np.insert(cut_beats, 0, 0.0)

    # ---- build clip-index sequence ----
    n_cuts = len(cut_beats)
    seq = _pick_clip_sequence(
        n_clips, n_cuts,
        mode=settings.clip_mode,
        stride=getattr(settings, "clip_stride", 2),
        avoid_recent=getattr(settings, "clip_avoid_recent", 2),
        rng=rng,
    )

    cuts: List[Cut] = []
    for i, t_start in enumerate(cut_beats):
        t_end = float(cut_beats[i + 1]) if i + 1 < len(cut_beats) else settings.duration
        if t_end <= t_start + 1.0 / settings.fps:
            continue
        t_start_f = float(t_start)

        # ── Musical context from AudioAnalysis (if supplied) ──
        if audio is not None:
            cut_energy   = audio.energy_at(t_start_f)
            cut_loudness = audio.rms_at(t_start_f)
            is_db        = audio.is_downbeat(t_start_f)
            is_drop      = audio.is_drop(t_start_f)
            # phrase boundary: every 4th downbeat
            if is_db and len(audio.downbeats) > 0:
                # index of nearest downbeat
                db_idx = int(np.argmin(np.abs(audio.downbeats - t_start_f)))
                is_phrase = (db_idx % 4 == 0)
            else:
                is_phrase = False
        else:
            cut_energy   = 0.5
            cut_loudness = 0.5
            is_db        = (i % 4 == 0)            # every 4th heuristic
            is_drop      = False
            is_phrase    = (i % 16 == 0)

        # ── energy-aware motion + transition selection ──
        motion, motion_amp = _choose_motion(rng, cut_energy, is_drop, is_db)
        transition = _choose_transition(rng, cut_energy, is_phrase, is_drop, i,
                                         getattr(settings, "transition_mode", "random"))
        # Random in-point in the source clip so we don't keep watching the
        # same opening frames over and over.
        clip_in_offset = 0.0
        if settings.random_clip_starts:
            # Look up clip duration: prefer pre-computed list, fall back to
            # lazy callable. Both paths avoid querying durations for clips
            # the timeline never uses — critical for huge media folders.
            cd: float = 0.0
            if clip_durations is not None and seq[i] < len(clip_durations):
                cd = clip_durations[seq[i]]
            elif get_duration is not None:
                try:
                    cd = float(get_duration(seq[i]))
                except Exception:
                    cd = 0.0
            cut_d  = float(t_end) - float(t_start)
            if cd > 0 and not math.isinf(cd):
                # Per-cut sub-RNG seeded with (master seed, clip_idx, cut idx)
                # so the same clip used in different cuts (or different
                # batch variants) lands on different offsets.
                sub = random.Random((seed * 1009 + seq[i] * 31 + i) & 0xFFFFFFFF)
                room = cd - cut_d - 0.2
                if room > 0.1:
                    clip_in_offset = sub.uniform(0.0, room)
                elif cd > 0.5:
                    # Clip is barely longer than the cut — at least pick
                    # *some* starting point, modulo will wrap if needed.
                    clip_in_offset = sub.uniform(0.0, cd * 0.5)

        # Chaos aspect is now applied continuously in the render loop
        # (time-driven X/Y scale envelope) — per-cut aspect labels are not
        # used.  Keep the field for backward compatibility with old preset
        # files but leave them empty.
        aspect_label = ""
        aspect_w = 0.0
        aspect_h = 0.0

        cuts.append(Cut(
            t_start=float(t_start),
            t_end=float(t_end),
            clip_idx=seq[i],
            preset_seed=rng.randint(0, 1_000_000),
            motion=motion,
            motion_amp=motion_amp,
            transition=transition,
            # overlays are more interesting on phrase boundaries
            overlay=(rng.random() < (0.10 if is_phrase else 0.04)),
            clip_in_offset=clip_in_offset,
            aspect_w=aspect_w,
            aspect_h=aspect_h,
            aspect_label=aspect_label,
            energy=float(cut_energy),
            loudness=float(cut_loudness),
            is_downbeat=is_db,
            is_phrase_start=is_phrase,
            is_drop=is_drop,
        ))

    # ---- assign text cards ----
    tc = settings.text_cards
    if tc.enabled:
        texts = tc.all_texts()
        if texts:
            tc_rng = random.Random(seed ^ 0xC0FFEE)
            for cut in cuts:
                # don't put a card on the very first cut or very short cuts
                if cut.t_start < 0.05:
                    continue
                cut_len = cut.t_end - cut.t_start
                if cut_len < tc.duration + 0.1:
                    continue
                if tc_rng.random() < tc.probability:
                    cut.text_card = tc_rng.choice(texts)
                    cut.text_card_dur = tc.duration

    return cuts


# ---------------------------------------------------------------- renderer ----

class OffscreenRenderer:
    """GL renderer used both for headless export and live preview.
    Pass `ctx=None` (default) → standalone context (no window).
    Pass an existing moderngl context (e.g. from a GLFW window) for preview."""

    def __init__(self, w: int, h: int, ctx: Optional[moderngl.Context] = None) -> None:
        self.w, self.h = w, h
        self.ctx = ctx if ctx is not None else moderngl.create_standalone_context()
        vert = (SHADER_DIR / "passthrough.vert").read_text(encoding="utf-8")

        # Limit the standalone version to a tight set of effects so the file
        # stays manageable. These mirror the live tool's most useful ones.
        EFFECT_SHADERS = [
            "luma_key", "duotone", "dual_tone", "heavy_grain",
            "motion_trails", "wave_warp", "pixel_sort",
            "datamosh", "vhs", "ascii_mosaic",
            "dither",
            "rgb_split", "edges", "pixel_swap",
            "kaleidoscope", "slit_scan", "distort",
            "thermal", "cellular", "elastic_grid", "crt_monitor", "glitch",
            "modulate", "pixelate",
            "found_footage", "chromatic_aberration", "color_posterize", "swirl",
            "iridescent_foil", "motion_blur", "advanced_glitch",
            "final",
        ]
        self.progs = {}
        for name in EFFECT_SHADERS:
            try:
                self.progs[name] = self.ctx.program(
                    vertex_shader=vert,
                    fragment_shader=(SHADER_DIR / f"{name}.frag").read_text(encoding="utf-8"),
                )
            except Exception as e:
                print(f"[automatch] shader '{name}' skipped: {e}")

        # GPU warp+composite shader (motion warp + crossfade in one GPU pass)
        try:
            self.prog_warp = self.ctx.program(
                vertex_shader=vert,
                fragment_shader=(SHADER_DIR / "warp_composite.frag").read_text(encoding="utf-8"),
            )
        except Exception as e:
            print(f"[automatch] warp_composite shader failed: {e}")
            self.prog_warp = None

        verts = np.array([
            -1, -1, 0, 0,   1, -1, 1, 0,   -1, 1, 0, 1,   1, 1, 1, 1,
        ], dtype="f4")
        self.vbo = self.ctx.buffer(verts.tobytes())
        self.vaos = {
            n: self.ctx.vertex_array(p, [(self.vbo, "2f 2f", "in_pos", "in_uv")])
            for n, p in self.progs.items()
        }
        if self.prog_warp is not None:
            self.vao_warp = self.ctx.vertex_array(
                self.prog_warp, [(self.vbo, "2f 2f", "in_pos", "in_uv")])
        else:
            self.vao_warp = None
        self.prog_uniforms = {n: set(p) for n, p in self.progs.items()}

        # ── logo-flash overlay (easter egg) ──────────────────────────────
        # A simple alpha-blended textured quad drawn ON TOP of the finished
        # frame so the effect chain can't eat it. Lazily-built RGBA texture.
        self.logo_tex = None
        self.logo_tex_size = None
        try:
            self.prog_logo = self.ctx.program(
                vertex_shader=vert,
                fragment_shader="""
                    #version 330
                    uniform sampler2D u_logo;
                    uniform float u_alpha;
                    in  vec2 v_uv;
                    out vec4 fragColor;
                    void main() {
                        vec4 c = texture(u_logo, v_uv);
                        fragColor = vec4(c.rgb, c.a * u_alpha);
                    }
                """,
            )
            self.vao_logo = self.ctx.vertex_array(
                self.prog_logo, [(self.vbo, "2f 2f", "in_pos", "in_uv")])
        except Exception as e:
            print(f"[automatch] logo overlay shader failed: {e}")
            self.prog_logo = None
            self.vao_logo = None

        # FBO ping-pong
        def fbo():
            t = self.ctx.texture((w, h), 4)
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            return t, self.ctx.framebuffer(color_attachments=[t])
        self.input_tex = self.ctx.texture((w, h), 3)
        self.input_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        # second texture for the "previous frame" used by warp_composite
        self.prev_tex = self.ctx.texture((w, h), 3)
        self.prev_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.ping = fbo(); self.pong = fbo()
        self.prev_trails = fbo()    # motion_trails ping-pong
        self.prev_dm = fbo()        # datamosh ping-pong
        self.prev_slit = fbo()      # slit_scan feedback accumulator
        self.prev_ca = fbo()        # cellular-automata state buffer
        self.prev_glitch = fbo()    # glitch freeze/repeat buffer
        self.prev_pixelate = fbo()  # pixelate recursive-feedback accumulator
        # ─── Chaos mask machinery ───────────────────────────────────────
        # mask_scratch is a side FBO we render into when masking an
        # in-place FBO (read+write to the same texture is forbidden in GL).
        self.mask_scratch = fbo()
        try:
            self.prog_mask = self.ctx.program(
                vertex_shader=vert,
                fragment_shader="""
                    #version 330
                    uniform sampler2D u_tex;
                    uniform vec4 u_bbox;            // x0, y0, x1, y1 in UV
                    in  vec2 v_uv;
                    out vec4 fragColor;
                    void main() {
                        bool inside = (v_uv.x >= u_bbox.x && v_uv.x <= u_bbox.z
                                    && v_uv.y >= u_bbox.y && v_uv.y <= u_bbox.w);
                        fragColor = inside ? texture(u_tex, v_uv)
                                           : vec4(0.0, 0.0, 0.0, 1.0);
                    }
                """,
            )
            self.vao_mask = self.ctx.vertex_array(
                self.prog_mask, [(self.vbo, "2f 2f", "in_pos", "in_uv")])
        except Exception as e:
            print(f"[automatch] chaos mask shader failed: {e}")
            self.prog_mask = None
            self.vao_mask = None
        # ASCII atlas — rebuilt live when params.ascii_text changes
        self.current_ascii_text = ASCII_DEFAULT
        atlas_arr, count, _ = make_atlas_array(ASCII_DEFAULT)
        self.ascii_tex = self.ctx.texture(
            (atlas_arr.shape[1], atlas_arr.shape[0]), 1, atlas_arr.tobytes())
        self.ascii_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.ascii_count = count

        # Blue noise tile for dither algo=3 (64×64, tileable)
        self.blue_noise_tex = self._bake_blue_noise_tex()

        # identity matrix (float32, column-major bytes for GLSL mat3)
        self._identity_mat3 = np.eye(3, dtype=np.float32).T.tobytes()

    def _U(self, name, key, value):
        if key in self.prog_uniforms.get(name, ()):
            self.progs[name][key] = value

    def _bake_blue_noise_tex(self, size: int = 64):
        """Generate a tileable blue-noise-like 64×64 byte tile via iterative
        high-pass filtering of white noise.  Not true void-and-cluster, but
        the spatial spectrum is dominated by high frequencies — visually
        indistinguishable from real blue noise at video resolution.

        Cached on disk so we don't regenerate every launch.
        """
        cache_path = Path(__file__).parent / "models" / f"blue_noise_{size}.npy"
        try:
            if cache_path.exists():
                arr = np.load(str(cache_path))
            else:
                rng = np.random.default_rng(0xB10E_5EED)
                arr = rng.random((size, size)).astype(np.float32)
                # iterative tileable HPF: subtract a blurred copy
                for _ in range(20):
                    blurred = (arr
                               + np.roll(arr,  1, 0) + np.roll(arr, -1, 0)
                               + np.roll(arr,  1, 1) + np.roll(arr, -1, 1)) / 5.0
                    arr = arr - 0.65 * (blurred - 0.5)
                arr = (arr - arr.min()) / max(float(np.ptp(arr)), 1e-6)
                cache_path.parent.mkdir(exist_ok=True)
                try:
                    np.save(str(cache_path), arr)
                except Exception:
                    pass
            byte = (np.clip(arr, 0.0, 1.0) * 255).astype(np.uint8)
            tex = self.ctx.texture((size, size), 1, byte.tobytes())
            tex.repeat_x = tex.repeat_y = True
            tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
            return tex
        except Exception as e:
            print(f"[automatch] blue noise bake failed: {e}")
            # fallback: white noise so dither algo=3 still works
            byte = (np.random.default_rng(0).random((size, size)) * 255).astype(np.uint8)
            tex = self.ctx.texture((size, size), 1, byte.tobytes())
            tex.repeat_x = tex.repeat_y = True
            tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
            return tex

    def _rebuild_ascii_atlas(self, text: str) -> None:
        """Re-bake the ASCII glyph atlas in place (called when the user
        edits the glyph string in the GUI)."""
        text = text or ASCII_DEFAULT
        if text == self.current_ascii_text:
            return
        try:
            arr, count, _ = make_atlas_array(text)
        except Exception as e:
            print(f"[automatch] ASCII atlas rebake failed: {e}")
            return
        try:
            self.ascii_tex.release()
        except Exception:
            pass
        self.ascii_tex = self.ctx.texture(
            (arr.shape[1], arr.shape[0]), 1, arr.tobytes())
        self.ascii_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.ascii_count = int(count) if count > 0 else 1
        self.current_ascii_text = text

    def _upload_bgr(self, tex, frame_bgr: np.ndarray) -> None:
        """Upload a BGR numpy frame to a GL texture (flipped for GL convention)."""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.flip(rgb, 0)
        tex.write(np.ascontiguousarray(rgb))

    def _ensure_logo_tex(self):
        """Lazily build the RGBA logo texture sized to the render target.

        RGB = white logo, A = ASCII coverage mask. Uploaded vertically flipped
        to match the GL bottom-up convention (same as _upload_bgr) so it lands
        upright on screen. Returns the texture or None if unavailable.
        """
        if self.prog_logo is None:
            return None
        if self.logo_tex is not None and self.logo_tex_size == (self.w, self.h):
            return self.logo_tex
        layer = _logo_flash_layer(self.w, self.h)
        if layer is None:
            return None
        mask, rgb = layer                       # mask (h,w) 0..1, rgb (h,w,3) uint8
        a = (np.clip(mask, 0.0, 1.0) * 255.0).astype(np.uint8)
        rgba = np.dstack([rgb, a])              # (h, w, 4) uint8 RGBA
        rgba = np.flip(rgba, axis=0)            # GL bottom-up
        if self.logo_tex is not None:
            try: self.logo_tex.release()
            except Exception: pass
        self.logo_tex = self.ctx.texture((self.w, self.h), 4,
                                         np.ascontiguousarray(rgba).tobytes())
        self.logo_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.logo_tex_size = (self.w, self.h)
        return self.logo_tex

    def _mat3_bytes(self, M: Optional[np.ndarray]) -> bytes:
        """Convert a 3×3 float64 pixel-UV warp matrix to GLSL mat3 bytes
        (column-major float32).  None → identity."""
        if M is None:
            return self._identity_mat3
        return M.T.astype(np.float32).tobytes()

    def _apply_chaos_mask_fbo(self, fbo_pair: tuple, bbox: tuple) -> None:
        """Zero everything outside bbox in the given (texture, fbo) pair.

        Reads the pair's texture, runs the mask shader into mask_scratch's
        FBO, then copies mask_scratch back into the original FBO.  No
        in-place read+write hazards.  Used between effect passes so the
        accumulating FBOs (motion_trails, datamosh ping-pong) only ever
        hold pixels from inside the chaos bbox.
        """
        if self.prog_mask is None or self.vao_mask is None:
            return
        tex, fbo_obj = fbo_pair
        # Render mask result into the scratch FBO
        self.mask_scratch[1].use()
        self.ctx.viewport = (0, 0, self.w, self.h)
        self.ctx.clear()
        tex.use(0)
        self.prog_mask["u_tex"] = 0
        self.prog_mask["u_bbox"] = (float(bbox[0]), float(bbox[1]),
                                    float(bbox[2]), float(bbox[3]))
        self.vao_mask.render(moderngl.TRIANGLE_STRIP)
        # Copy scratch back to original FBO
        self.ctx.copy_framebuffer(fbo_obj, self.mask_scratch[1])

    def reset_feedback(self):
        """Clear every feedback accumulator FBO to transparent black.

        Called on each cut boundary so frame-recursive effects (motion_trails,
        datamosh, slit_scan, cellular, glitch) restart fresh on every clip
        instead of bleeding their state across the stitch.  This enforces the
        "effect is applied per-frame, then frames are stitched" model: no
        feedback memory survives a cut.
        """
        for tex, fb in (self.prev_trails, self.prev_dm, self.prev_slit,
                        self.prev_ca, self.prev_glitch, self.prev_pixelate):
            fb.clear(0.0, 0.0, 0.0, 0.0)

    def render(self, frame_bgr: np.ndarray, params, t: float,
               prev_frame_bgr: Optional[np.ndarray] = None,
               warp_cur:  Optional[np.ndarray] = None,
               warp_prev: Optional[np.ndarray] = None,
               fade_in:   float = 1.0,
               present_screen_size: Optional[tuple] = None,
               chaos_bbox: Optional[tuple] = None,
               logo_alpha: float = 0.0):
        """Run the pipeline on one source frame.

        GPU warp+crossfade path
        -----------------------
        When ``prev_frame_bgr`` is supplied and ``fade_in < 1.0`` the
        *warp_composite* shader blends the two warped frames on the GPU,
        replacing the CPU ``cv2.warpAffine`` + ``cv2.addWeighted`` calls.

        ``warp_cur`` / ``warp_prev``
            3×3 float64 pixel-UV backward warp matrices from
            ``_build_affine_mat()``.  ``None`` → identity (no motion).

        ``fade_in``
            0.0 = full previous frame, 1.0 = full current frame.

        If ``present_screen_size`` is None → return BGR ndarray (export mode).
        Else → draw to screen at that size (preview mode), returns None.
        """
        # Upload current frame
        self._upload_bgr(self.input_tex, frame_bgr)

        # ---- initial blit / warp+composite → ping -------------------------
        self.ping[1].use()
        self.ctx.clear()

        use_gpu_warp = (
            self.prog_warp is not None
            and prev_frame_bgr is not None
            and fade_in < 0.9999
        )

        if use_gpu_warp:
            # GPU path: warp both frames and blend in one pass
            self._upload_bgr(self.prev_tex, prev_frame_bgr)
            self.input_tex.use(0)
            self.prev_tex.use(1)
            self.prog_warp["u_cur"]  = 0
            self.prog_warp["u_prev"] = 1
            self.prog_warp["u_fade"] = float(fade_in)
            self.prog_warp["u_warp_cur"].write(self._mat3_bytes(warp_cur))
            self.prog_warp["u_warp_prev"].write(self._mat3_bytes(warp_prev))
            self.vao_warp.render(moderngl.TRIANGLE_STRIP)
        else:
            # Simple blit (no previous frame / fully faded in)
            self.input_tex.use(0)
            if "final" in self.progs:
                self._U("final", "u_tex", 0)
                self._U("final", "u_gain", 1.0)
                self._U("final", "u_contrast", 1.0)
                self._U("final", "u_saturation", 1.0)
                self._U("final", "u_flip_y", 0)
                self.vaos["final"].render(moderngl.TRIANGLE_STRIP)

        src, dst = self.ping, self.pong
        # Mask the source FBO before effects start — motion warp + crossfade
        # can drag black pixels into the bbox interior.
        if chaos_bbox is not None:
            self._apply_chaos_mask_fbo(src, chaos_bbox)
        res = (float(self.w), float(self.h))

        for cfg in params.effects:
            if not cfg.enabled:
                continue
            # pixel_sort is always full-wet — no partial mix, no random mix.
            eff_mix = 1.0 if cfg.kind == "pixel_sort" else cfg.mix
            if eff_mix < 1e-4:
                continue
            kind = cfg.kind
            if kind not in self.progs:
                continue
            dst[1].use()
            self.ctx.clear()
            src[0].use(0)
            # bind extras
            if kind == "motion_trails":
                self._U(kind, "u_current", 0)
                self.prev_trails[0].use(1)
                self._U(kind, "u_prev", 1)
            elif kind == "slit_scan":
                self._U(kind, "u_current", 0)
                self.prev_slit[0].use(1)
                self._U(kind, "u_prev", 1)
            elif kind == "pixelate":
                self._U(kind, "u_current", 0)
                self.prev_pixelate[0].use(1)
                self._U(kind, "u_prev", 1)
            elif kind == "cellular":
                self._U(kind, "u_tex", 0)
                self.prev_ca[0].use(1)
                self._U(kind, "u_prev", 1)
            elif kind == "glitch":
                self._U(kind, "u_tex", 0)
                self.prev_glitch[0].use(1)
                self._U(kind, "u_prev", 1)
            elif kind == "datamosh":
                self._U(kind, "u_tex", 0)
                self.prev_dm[0].use(1)
                self._U(kind, "u_prev", 1)
                # no optical-flow texture in offline mode → bind src as flow
                src[0].use(2)
                self._U(kind, "u_flow", 2)
            elif kind == "ascii_mosaic":
                # live-update atlas if the user typed something new
                wanted = getattr(params, "ascii_text", "") or ASCII_DEFAULT
                if wanted != self.current_ascii_text:
                    self._rebuild_ascii_atlas(wanted)
                self._U(kind, "u_tex", 0)
                self.ascii_tex.use(1)
                self._U(kind, "u_atlas", 1)
                self._U(kind, "u_glyph_count", float(self.ascii_count))
            elif kind == "dither":
                self._U(kind, "u_tex", 0)
                # bind blue noise tile to slot 1 (used only when algo==3)
                if self.blue_noise_tex is not None:
                    self.blue_noise_tex.use(1)
                    self._U(kind, "u_blue_noise", 1)
            else:
                self._U(kind, "u_tex", 0)
            # generic uniforms
            self._U(kind, "u_mix", eff_mix)
            # per-effect compositing mode (TouchDesigner-style Composite TOP);
            # _U safely skips shaders that don't declare u_blend.
            self._U(kind, "u_blend", float(getattr(cfg, "blend", 0.0)))
            self._U(kind, "u_time", t)
            self._U(kind, "u_resolution", res)
            self._set_effect_params(kind, params)
            self.vaos[kind].render(moderngl.TRIANGLE_STRIP)
            # ─── Chaos mask: clamp this effect's output to the bbox ───
            # Effects with neighbour-sampling (bloom, blur, drift) can
            # smear black into the bbox or vice-versa; running the mask
            # right after the effect ensures the dst FBO ONLY contains
            # active-area pixels.
            if chaos_bbox is not None:
                self._apply_chaos_mask_fbo(dst, chaos_bbox)
            if kind == "motion_trails":
                self.ctx.copy_framebuffer(self.prev_trails[1], dst[1])
                # also mask the prev_trails accumulator so old frames
                # outside the current bbox don't leak back in
                if chaos_bbox is not None:
                    self._apply_chaos_mask_fbo(self.prev_trails, chaos_bbox)
            if kind == "datamosh":
                self.ctx.copy_framebuffer(self.prev_dm[1], dst[1])
                if chaos_bbox is not None:
                    self._apply_chaos_mask_fbo(self.prev_dm, chaos_bbox)
            if kind == "slit_scan":
                self.ctx.copy_framebuffer(self.prev_slit[1], dst[1])
                if chaos_bbox is not None:
                    self._apply_chaos_mask_fbo(self.prev_slit, chaos_bbox)
            if kind == "pixelate":
                self.ctx.copy_framebuffer(self.prev_pixelate[1], dst[1])
                if chaos_bbox is not None:
                    self._apply_chaos_mask_fbo(self.prev_pixelate, chaos_bbox)
            if kind == "cellular":
                self.ctx.copy_framebuffer(self.prev_ca[1], dst[1])
                if chaos_bbox is not None:
                    self._apply_chaos_mask_fbo(self.prev_ca, chaos_bbox)
            if kind == "glitch":
                self.ctx.copy_framebuffer(self.prev_glitch[1], dst[1])
                if chaos_bbox is not None:
                    self._apply_chaos_mask_fbo(self.prev_glitch, chaos_bbox)
            src, dst = dst, src

        if present_screen_size is not None:
            # draw to default framebuffer (the GLFW window)
            fb_w, fb_h = present_screen_size
            self.ctx.screen.use()
            self.ctx.viewport = (0, 0, fb_w, fb_h)
            self.ctx.clear()
            src[0].use(0)
            self._U("final", "u_tex", 0)
            self._U("final", "u_gain", float(params.gain))
            self._U("final", "u_contrast", float(params.contrast))
            self._U("final", "u_saturation", float(params.saturation))
            self._U("final", "u_flip_y", 0)
            # ── Chaos void mask: keep the dark canvas pure black ──
            if chaos_bbox is not None:
                self._U("final", "u_use_mask", 1)
                # Final shader reads UV with v already in screen orientation,
                # so we flip Y to match: (x0, 1-y1, x1, 1-y0).
                x0, y0, x1, y1 = chaos_bbox
                self._U("final", "u_mask_bbox",
                        (float(x0), 1.0 - float(y1),
                         float(x1), 1.0 - float(y0)))
            else:
                self._U("final", "u_use_mask", 0)
                self._U("final", "u_mask_bbox", (0.0, 0.0, 1.0, 1.0))
            self.vaos["final"].render(moderngl.TRIANGLE_STRIP)
            # ── logo flash: alpha-blend on top of the finished screen ──
            if logo_alpha > 0.0 and self.prog_logo is not None:
                ltex = self._ensure_logo_tex()
                if ltex is not None:
                    self.ctx.enable(moderngl.BLEND)
                    self.ctx.blend_func = (moderngl.SRC_ALPHA,
                                           moderngl.ONE_MINUS_SRC_ALPHA)
                    ltex.use(0)
                    self.prog_logo["u_logo"] = 0
                    self.prog_logo["u_alpha"] = float(min(1.0, max(0.0, logo_alpha)))
                    self.vao_logo.render(moderngl.TRIANGLE_STRIP)
                    self.ctx.disable(moderngl.BLEND)
            return None

        # readback (export path)
        buf = src[1].read(components=3, dtype="f1")
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
        arr = np.ascontiguousarray(arr[::-1])
        # ── Chaos void mask: zero pixels outside the bbox on the CPU
        # (export path skips the final shader, so we mask here instead). ──
        if chaos_bbox is not None:
            H, W = arr.shape[:2]
            x0, y0, x1, y1 = chaos_bbox
            ix0 = max(0, min(W, int(round(x0 * W))))
            iy0 = max(0, min(H, int(round(y0 * H))))
            ix1 = max(0, min(W, int(round(x1 * W))))
            iy1 = max(0, min(H, int(round(y1 * H))))
            if iy0 > 0:           arr[:iy0, :] = 0
            if iy1 < H:           arr[iy1:, :] = 0
            if ix0 > 0:           arr[iy0:iy1, :ix0] = 0
            if ix1 < W:           arr[iy0:iy1, ix1:] = 0
        out_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        # ── logo flash: composite on top of the finished frame (CPU) ──
        if logo_alpha > 0.0:
            out_bgr = _overlay_ascii_logo(out_bgr, logo_alpha)
        return out_bgr

    def _set_effect_params(self, kind, params):
        U = lambda k, v: self._U(kind, k, v)
        if kind == "luma_key":
            U("u_lift", params.luma_lift)
            U("u_gain", params.luma_gain)
            U("u_gamma", params.luma_gamma)
            U("u_invert", params.luma_invert)
        elif kind == "duotone":
            U("u_accent", (params.accent_r, params.accent_g, params.accent_b))
            U("u_shadow", (0.0, 0.0, 0.0))
            U("u_midpoint", params.duotone_midpoint)
            U("u_strength", params.duotone_strength)
        elif kind == "dual_tone":
            U("u_shadow",    (params.dual_shadow_r, params.dual_shadow_g, params.dual_shadow_b))
            U("u_mid",       (params.dual_mid_r,    params.dual_mid_g,    params.dual_mid_b))
            U("u_highlight", (params.dual_high_r,   params.dual_high_g,   params.dual_high_b))
            U("u_split",     params.dual_split)
            U("u_strength",  params.dual_strength)
        elif kind == "heavy_grain":
            U("u_amount",    params.grain_amount)
            U("u_size",      params.grain_size)
            U("u_chroma",    params.grain_chroma)
            U("u_blotch",    params.grain_blotch)
            U("u_algorithm", getattr(params, "grain_algorithm", 0.0))
        elif kind == "motion_trails":
            U("u_decay",     params.trails_decay)
            U("u_threshold", params.trails_threshold)
            U("u_blur",      params.trails_blur)
            U("u_drift",     (params.trails_drift_x, params.trails_drift_y))
            U("u_tint",      (params.trails_tint_r, params.trails_tint_g, params.trails_tint_b))
            U("u_secondary", params.trails_secondary)
            U("u_mode",      params.trails_mode)
            U("u_polarity",  params.trails_polarity)
            U("u_chaos",     params.trails_chaos)
        elif kind == "wave_warp":
            U("u_amp",   (params.wave_amp1,   params.wave_amp2,   params.wave_amp3))
            U("u_freq",  (params.wave_freq1,  params.wave_freq2,  params.wave_freq3))
            U("u_phase", (params.wave_phase1, params.wave_phase2, params.wave_phase3))
            U("u_speed", (params.wave_speed1, params.wave_speed2, params.wave_speed3))
            U("u_shape", (params.wave_shape1, params.wave_shape2, params.wave_shape3))
            U("u_angle",       params.wave_angle)
            U("u_polar",       params.wave_polar)
            U("u_chromatic",   params.wave_chromatic)
            U("u_band_height", params.wave_band_height)
            U("u_band_y",      params.wave_band_y)
            U("u_band_soft",   params.wave_band_soft)
        elif kind == "pixel_sort":
            U("u_threshold",    params.psort_threshold)
            U("u_length",       params.psort_length)
            U("u_direction",    params.psort_direction)
            U("u_band_density", params.psort_band_density)
            U("u_band_jitter",  params.psort_band_jitter)
            U("u_band_speed",   params.psort_band_speed)
            U("u_length_var",   params.psort_length_var)
            U("u_edge_fade",    params.psort_edge_fade)
            U("u_color_mode",   params.psort_color_mode)
            U("u_axis",         params.psort_axis)
            U("u_algorithm",    params.psort_algorithm)
            U("u_chromatic",    params.psort_chromatic)
            U("u_blend",        params.psort_blend)
        elif kind == "datamosh":
            U("u_strength",     params.datamosh_strength)
            U("u_freeze",       params.datamosh_freeze)
            U("u_period",       params.datamosh_period)
            U("u_breakthrough", params.datamosh_breakthrough)
            U("u_jitter",       params.datamosh_jitter)
            U("u_chaos",        params.datamosh_chaos)
            U("u_algorithm",    getattr(params, "datamosh_algorithm", 1.0))
        elif kind == "vhs":
            U("u_tracking_noise", params.vhs_tracking_noise)
            U("u_line_jitter",    params.vhs_line_jitter)
            U("u_chroma_blur",    params.vhs_chroma_blur)
            U("u_color_loss",     params.vhs_color_loss)
            U("u_head_switch",    params.vhs_head_switch)
        elif kind == "ascii_mosaic":
            U("u_cell_size",     params.ascii_cell_size)
            U("u_invert",        params.ascii_invert)
            U("u_color_source",  params.ascii_color_source)
            U("u_tint",          (params.ascii_tint_r, params.ascii_tint_g, params.ascii_tint_b))
        elif kind == "dither":
            U("u_levels",          params.dither_levels)
            U("u_grid_size",       params.dither_grid_size)
            U("u_osc_a_freq",      params.dither_osc_a_freq)
            U("u_osc_b_freq",      params.dither_osc_b_freq)
            U("u_osc_a_speed",     params.dither_osc_a_speed)
            U("u_osc_b_speed",     params.dither_osc_b_speed)
            U("u_mix_mode",        params.dither_mix_mode)
            U("u_trace_thickness", params.dither_trace_thickness)
            U("u_jitter",          params.dither_jitter)
            U("u_chromatic",       params.dither_chromatic)
            U("u_polarity",        params.dither_polarity)
            U("u_color_mode",      params.dither_color_mode)
            U("u_algorithm",       getattr(params, "dither_algorithm", 0.0))
            U("u_color_a", (params.dither_color_a_r,
                            params.dither_color_a_g,
                            params.dither_color_a_b))
            U("u_color_b", (params.dither_color_b_r,
                            params.dither_color_b_g,
                            params.dither_color_b_b))
        elif kind == "rgb_split":
            U("u_shift",      params.rgb_shift_amount)
            U("u_wave_freq",  params.rgb_wave_frequency)
            U("u_wave_speed", params.rgb_wave_speed)
        elif kind == "edges":
            U("u_accent",     (params.accent_r, params.accent_g, params.accent_b))
            U("u_threshold",  params.edges_threshold)
            U("u_thickness",  params.edges_thickness)
            U("u_fade_image", params.edges_fade_image)
        elif kind == "pixel_swap":
            U("u_threshold",   params.pixel_swap_threshold)
            U("u_radius",      params.pixel_swap_radius)
            U("u_chaos",       params.pixel_swap_chaos)
            U("u_directional", params.pixel_swap_directional)
            U("u_temporal",    params.pixel_swap_temporal)
        elif kind == "kaleidoscope":
            U("u_sides", getattr(params, "kaleido_sides", 6.0))
            U("u_angle", getattr(params, "kaleido_angle", 0.0))
            U("u_spin",  getattr(params, "kaleido_spin",  0.10))
            U("u_zoom",  getattr(params, "kaleido_zoom",  1.0))
        elif kind == "slit_scan":
            U("u_direction", getattr(params, "slit_direction", 0.0))
            U("u_speed",     getattr(params, "slit_speed",     0.5))
            U("u_scanpos",   getattr(params, "slit_scanpos",   0.5))
            U("u_feedback",  getattr(params, "slit_feedback",  1.0))
            U("u_threshold", getattr(params, "slit_threshold", 0.0))
        elif kind == "distort":
            U("u_xshift",    getattr(params, "distort_xshift",    0.5))
            U("u_yshift",    getattr(params, "distort_yshift",    0.5))
            U("u_threshold", getattr(params, "distort_threshold", 0.0))
            U("u_scale",     getattr(params, "distort_scale",     4.0))
            U("u_speed",     getattr(params, "distort_speed",     0.3))
            U("u_blur",      getattr(params, "distort_blur",      0.0))
            U("u_grain",     getattr(params, "distort_grain",     0.0))
            U("u_gamma",     getattr(params, "distort_gamma",     1.0))
            U("u_black",     getattr(params, "distort_black",     0.0))
            U("u_white",     getattr(params, "distort_white",     1.0))
            U("u_noise_mode", getattr(params, "distort_noise_mode", 0.0))
        elif kind == "thermal":
            U("u_palette", getattr(params, "thermal_palette", 0.0))
            U("u_gain",    getattr(params, "thermal_gain",    1.0))
            U("u_gamma",   getattr(params, "thermal_gamma",   1.0))
            U("u_levels",  getattr(params, "thermal_levels",  0.0))
        elif kind == "cellular":
            U("u_cell",     getattr(params, "ca_cell",     8.0))
            U("u_rate",     getattr(params, "ca_rate",     0.5))
            U("u_seed_thr", getattr(params, "ca_seed_thr", 0.6))
            U("u_mode",     getattr(params, "ca_mode",     0.0))
            U("u_tint",     (getattr(params, "ca_tint_r", 0.31),
                             getattr(params, "ca_tint_g", 1.0),
                             getattr(params, "ca_tint_b", 0.55)))
        elif kind == "elastic_grid":
            U("u_grid",       getattr(params, "eg_grid",       8.0))
            U("u_strength",   getattr(params, "eg_strength",   0.5))
            U("u_speed",      getattr(params, "eg_speed",      0.5))
            U("u_jitter",     getattr(params, "eg_jitter",     0.5))
            U("u_noise_mode", getattr(params, "eg_noise_mode", 0.0))
        elif kind == "crt_monitor":
            U("u_curvature",      getattr(params, "crtm_curvature",      0.30))
            U("u_scanline",       getattr(params, "crtm_scanline",       0.50))
            U("u_grille",         getattr(params, "crtm_grille",         0.40))
            U("u_vignette",       getattr(params, "crtm_vignette",       0.40))
            U("u_bloom",          getattr(params, "crtm_bloom",          0.30))
            U("u_aberration",     getattr(params, "crtm_aberration",     0.50))
            U("u_aberration_mode",getattr(params, "crtm_aberration_mode",0.0))
        elif kind == "glitch":
            U("u_block_size",    getattr(params, "glitch_block_size",    0.30))
            U("u_drop_chance",   getattr(params, "glitch_drop_chance",   0.30))
            U("u_repeat_chance", getattr(params, "glitch_repeat_chance", 0.20))
        elif kind == "modulate":
            U("u_mode",   getattr(params, "mod_mode",   0.0))
            U("u_freq",   getattr(params, "mod_freq",   20.0))
            U("u_amount", getattr(params, "mod_amount", 0.5))
            U("u_speed",  getattr(params, "mod_speed",  0.5))
            U("u_axis",   getattr(params, "mod_axis",   0.0))
        elif kind == "pixelate":
            U("u_block",    getattr(params, "pixelate_block",    12.0))
            U("u_feedback", getattr(params, "pixelate_feedback", 0.90))
            U("u_zoom",     getattr(params, "pixelate_zoom",     0.55))
            U("u_drift",    getattr(params, "pixelate_drift",    0.30))
            U("u_fb_mode",  getattr(params, "pixelate_fb_mode",  0.0))
        elif kind == "found_footage":
            U("u_dropout_density",  getattr(params, "ff_dropout_density",  0.4))
            U("u_dropout_strength", getattr(params, "ff_dropout_strength", 0.7))
            U("u_warp_amount",      getattr(params, "ff_warp_amount",      0.008))
            U("u_ghost_opacity",    getattr(params, "ff_ghost_opacity",    0.12))
            U("u_chroma_bleed",     getattr(params, "ff_chroma_bleed",     2.5))
            U("u_luma_crush",       getattr(params, "ff_luma_crush",       0.4))
        elif kind == "chromatic_aberration":
            U("u_offset",          getattr(params, "ca_offset",       0.012))
            U("u_radial",          getattr(params, "ca_radial",       0.8))
            U("u_angle",           getattr(params, "ca_angle",        0.0))
            U("u_chromatic_spin",  getattr(params, "ca_spin",         0.0))
        elif kind == "color_posterize":
            U("u_levels",      getattr(params, "post_levels",     6.0))
            U("u_color_mode",  getattr(params, "post_color_mode", 0.0))
            U("u_dither_mix",  getattr(params, "post_dither_mix", 0.3))
        elif kind == "swirl":
            U("u_strength", getattr(params, "swirl_strength", 3.0))
            U("u_radius",   getattr(params, "swirl_radius",   0.5))
            U("u_cx",       getattr(params, "swirl_cx",       0.5))
            U("u_cy",       getattr(params, "swirl_cy",       0.5))
            U("u_animate",  getattr(params, "swirl_animate",  0.0))
        elif kind == "iridescent_foil":
            U("u_hue_range", getattr(params, "ir_hue_range", 0.5))
            U("u_speed",     getattr(params, "ir_speed",     0.4))
            U("u_metallic",  getattr(params, "ir_metallic",  0.6))
            U("u_scale",     getattr(params, "ir_scale",     1.5))
            U("u_opacity",   getattr(params, "ir_opacity",   0.7))
        elif kind == "motion_blur":
            U("u_samples",  int(getattr(params, "mb_samples",  8.0)))  # u_samples is `uniform int`
            U("u_length",   getattr(params, "mb_length",   0.012))
            U("u_angle",    getattr(params, "mb_angle",    0.0))
            U("u_radial",   getattr(params, "mb_radial",   0.0))
            U("u_center_x", getattr(params, "mb_cx",       0.5))
            U("u_center_y", getattr(params, "mb_cy",       0.5))
        elif kind == "advanced_glitch":
            U("u_block_size",   getattr(params, "ag_block_size",   0.3))
            U("u_shift_amount", getattr(params, "ag_shift_amount", 0.04))
            U("u_chance",       getattr(params, "ag_chance",       0.4))
            U("u_color_shift",  getattr(params, "ag_color_shift",  0.4))
            U("u_speed",        getattr(params, "ag_speed",        2.0))


# ---------------------------------------------------------------- main flow ----

def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _beat_remap_time(t_out: float, t_abs_start: float,
                     beats: np.ndarray, strength: float) -> float:
    """Remap linear clip-local time using beat phases.

    Speed curve: s(φ) = 1 + A·cos(2π·φ)   where φ ∈ [0,1) is the normalised
    position within the current beat period (φ=0 means ON the beat).

    Effect: fast right on the beat, slow between beats — the "punch" feel.
    Integral →  t_media = t_out + A·T/(2π) · sin(2π·φ)

    strength 0.0 = no remap  /  0.3 = subtle  /  0.7 = dramatic
    A is capped so minimum speed ≥ 0.2× (no full freeze).
    """
    if strength < 0.01 or beats.size < 2:
        return t_out

    t_abs  = t_abs_start + t_out

    # find the two surrounding beat times
    prev_b: float = float(beats[0])
    next_b: float = float(beats[-1]) + (float(beats[-1]) - float(beats[-2]))
    for b in beats:
        bf = float(b)
        if bf <= t_abs + 1e-6:
            prev_b = bf
        else:
            next_b = bf
            break

    T     = max(0.05, next_b - prev_b)           # beat period (s)
    phase = (t_abs - prev_b) / T                  # 0..1 within beat period
    A     = min(strength * 0.75, 0.80)            # amplitude — capped at 0.8
    offset = A * T / (2.0 * math.pi) * math.sin(2.0 * math.pi * phase)
    return max(0.0, t_out + offset)


def _build_affine_mat(motion: str, amp: float, t_in_cut: float,
                      cut_dur: float, seed: int, w: int, h: int) -> np.ndarray:
    """Return a 3×3 float64 backward warp matrix in *pixel-UV* space.

    "pixel-UV" = u = x/w, v = y/h  (v increases downward, same as image rows).
    The warp_composite.frag shader expects this matrix, uploaded column-major:
        program["u_warp_cur"].write(mat.T.astype("f4").tobytes())

    For no motion this is the identity matrix.
    """
    if motion == "none" or amp < 0.01 or cut_dur <= 0 or w <= 0 or h <= 0:
        return np.eye(3, dtype=np.float64)

    progress = max(0.0, min(1.0, t_in_cut / cut_dur))
    p        = _smoothstep(progress)
    cx, cy   = w * 0.5, h * 0.5
    rng      = random.Random(seed)

    def _rot_scale(scale: float, tx: float, ty: float, rot_deg: float) -> np.ndarray:
        """Build a 2×3 pixel-space forward affine (same logic as apply_motion)."""
        c = scale * math.cos(math.radians(rot_deg))
        s = scale * math.sin(math.radians(rot_deg))
        return np.array([
            [ c,  s, (1 - c) * cx - s * cy + tx],
            [-s,  c,  s * cx + (1 - c) * cy + ty],
        ], dtype=np.float64)

    if motion == "zoom_in":
        M2 = _rot_scale(1.0 + amp * 0.35 * p, 0, 0, 0)
    elif motion == "zoom_out":
        M2 = _rot_scale(1.0 + amp * 0.35 * (1.0 - p), 0, 0, 0)
    elif motion == "pan_lr":
        tx = (p - 0.5) * 2.0 * amp * w * 0.15
        M2 = _rot_scale(1.0 + amp * 0.05, tx, 0, 0)
    elif motion == "pan_ud":
        ty = (p - 0.5) * 2.0 * amp * h * 0.15
        M2 = _rot_scale(1.0 + amp * 0.05, 0, ty, 0)
    elif motion == "shake":
        rng2 = random.Random(seed + int(t_in_cut * 60))
        tx   = (rng2.random() - 0.5) * amp * 30
        ty   = (rng2.random() - 0.5) * amp * 30
        rot  = (rng2.random() - 0.5) * amp * 3
        M2   = _rot_scale(1.0 + amp * 0.05, tx, ty, rot)
    elif motion == "rotate_cw":
        M2 = _rot_scale(1.0 + amp * 0.1, 0, 0,  amp * 12 * p)
    elif motion == "rotate_ccw":
        M2 = _rot_scale(1.0 + amp * 0.1, 0, 0, -amp * 12 * p)
    elif motion == "ken_burns":
        ang   = rng.uniform(0, 6.283)
        scale = 1.0 + amp * 0.30 * p
        tx    = math.cos(ang) * amp * w * 0.10 * p
        ty    = math.sin(ang) * amp * h * 0.10 * p
        M2    = _rot_scale(scale, tx, ty, 0)
    elif motion == "glitch_jump":
        step = int(t_in_cut * 10) + seed
        rng2 = random.Random(step)
        tx   = (rng2.random() - 0.5) * amp * w * 0.08
        ty   = (rng2.random() - 0.5) * amp * h * 0.08
        M2   = _rot_scale(1.0 + amp * 0.05, tx, ty, 0)
    else:
        return np.eye(3, dtype=np.float64)

    # --- convert 2×3 pixel-space forward transform → 3×3 pixel-UV backward ---
    # 1.  Embed into 3×3 homogeneous matrix
    M3         = np.eye(3, dtype=np.float64)
    M3[:2, :]  = M2
    # 2.  Invert: gives backward pixel→pixel mapping
    try:
        M3_inv = np.linalg.inv(M3)
    except np.linalg.LinAlgError:
        return np.eye(3, dtype=np.float64)
    # 3.  Scale: pixel ↔ UV  (S: UV→px, S_inv: px→UV)
    S     = np.diag([float(w), float(h), 1.0])
    S_inv = np.diag([1.0 / w,  1.0 / h, 1.0])
    # result: given dst UV, returns src UV (both in pixel-UV convention)
    return S_inv @ M3_inv @ S


# ---------------------------------------------------------------- transitions ----

def _trans_crossfade(prev, cur, a):
    a = _smoothstep(a)
    return cv2.addWeighted(prev, 1.0 - a, cur, a, 0)


def _trans_luma_wipe(prev, cur, a):
    """`cur` reveals through `prev`'s bright pixels first, then dark ones.
    Soft mask gives a glowing dissolve."""
    luma = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    threshold = 1.0 - _smoothstep(a)
    feather = 0.12
    mask = np.clip((luma - threshold + feather) / max(feather * 2, 1e-3), 0.0, 1.0)
    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    mask3 = np.stack([mask, mask, mask], axis=-1)
    return (cur.astype(np.float32) * mask3 +
            prev.astype(np.float32) * (1.0 - mask3)).astype(np.uint8)


def _trans_pip_zoom(prev, cur, a):
    """cur appears as a small box that grows from the centre to fullscreen."""
    h, w = prev.shape[:2]
    a = _smoothstep(a)
    if a < 0.05:
        return prev
    iw = max(2, int(w * a))
    ih = max(2, int(h * a))
    small = cv2.resize(cur, (iw, ih), interpolation=cv2.INTER_AREA)
    out = prev.copy()
    x0 = (w - iw) // 2
    y0 = (h - ih) // 2
    out[y0:y0 + ih, x0:x0 + iw] = small
    return out


def _trans_slide(prev, cur, a, dx=1, dy=0):
    """cur slides in from one edge, pushing prev out."""
    h, w = prev.shape[:2]
    a = _smoothstep(a)
    off_x = int(w * (1.0 - a) * dx)
    off_y = int(h * (1.0 - a) * dy)
    out = prev.copy()
    if dx != 0:
        if dx > 0:                          # cur from right
            src_w = w - off_x
            if src_w > 0:
                out[:, off_x:] = cur[:, :src_w]
        else:                                # cur from left
            src_w = w + off_x   # off_x is negative
            if src_w > 0:
                out[:, :src_w] = cur[:, -src_w:]
    elif dy != 0:
        if dy > 0:                          # cur from bottom
            src_h = h - off_y
            if src_h > 0:
                out[off_y:, :] = cur[:src_h, :]
        else:                                # cur from top
            src_h = h + off_y
            if src_h > 0:
                out[:src_h, :] = cur[-src_h:, :]
    return out


def _trans_wipe_diag(prev, cur, a):
    """Diagonal wipe from top-left to bottom-right."""
    h, w = prev.shape[:2]
    a = _smoothstep(a)
    # build a mask where pixels with x/w + y/h < 2*a are revealed
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    mask = ((xs / w + ys / h) < 2.0 * a).astype(np.float32)
    mask = cv2.GaussianBlur(mask, (7, 7), 0)
    mask3 = np.stack([mask, mask, mask], axis=-1)
    return (cur.astype(np.float32) * mask3 +
            prev.astype(np.float32) * (1.0 - mask3)).astype(np.uint8)


def _trans_datamosh_freeze(prev, cur, a):
    """The CPU side just holds `prev` longer than usual and then dissolves;
    the actual motion-vector ghosting is the shader datamosh effect, which we
    crank up in params during the transition window (see run/preview loops)."""
    # 0.65 hold-and-warp window (shader does the ghost work here), 0.35 dissolve
    if a < 0.65:
        return prev
    a2 = (a - 0.65) / 0.35
    return _trans_crossfade(prev, cur, a2)


def apply_transition(prev, cur, alpha: float, kind: str):
    if kind == "luma_wipe":      return _trans_luma_wipe(prev, cur, alpha)
    if kind == "pip_zoom":       return _trans_pip_zoom(prev, cur, alpha)
    if kind == "slide_left":     return _trans_slide(prev, cur, alpha, dx=-1)
    if kind == "slide_right":    return _trans_slide(prev, cur, alpha, dx=+1)
    if kind == "slide_up":       return _trans_slide(prev, cur, alpha, dy=-1)
    if kind == "wipe_diag":      return _trans_wipe_diag(prev, cur, alpha)
    if kind == "datamosh_freeze":return _trans_datamosh_freeze(prev, cur, alpha)
    return _trans_crossfade(prev, cur, alpha)


# ---------------------------------------------------------------- motions ----

def apply_motion(frame, motion: str, amp: float, t_in_cut: float,
                 cut_duration: float, seed: int = 0):
    """Apply an AE-style geometric motion to a single frame. Returns same shape.
    `t_in_cut` ∈ [0, cut_duration); progress = t_in_cut / cut_duration."""
    h, w = frame.shape[:2]
    if motion == "none" or amp < 0.01 or cut_duration <= 0:
        return frame
    progress = max(0.0, min(1.0, t_in_cut / cut_duration))
    p = _smoothstep(progress)

    cx, cy = w * 0.5, h * 0.5

    # Build a 2×3 affine matrix M and apply it once.
    def affine(scale, tx, ty, rot_deg):
        # rotate+scale around centre, then translate
        M = cv2.getRotationMatrix2D((cx, cy), rot_deg, scale)
        M[0, 2] += tx
        M[1, 2] += ty
        return M

    rng = random.Random(seed)
    if motion == "zoom_in":
        scale = 1.0 + amp * 0.35 * p
        M = affine(scale, 0, 0, 0)
    elif motion == "zoom_out":
        scale = 1.0 + amp * 0.35 * (1.0 - p)
        M = affine(scale, 0, 0, 0)
    elif motion == "pan_lr":
        tx = (p - 0.5) * 2.0 * amp * w * 0.15
        M = affine(1.0 + amp * 0.05, tx, 0, 0)
    elif motion == "pan_ud":
        ty = (p - 0.5) * 2.0 * amp * h * 0.15
        M = affine(1.0 + amp * 0.05, 0, ty, 0)
    elif motion == "shake":
        rng2 = random.Random(seed + int(t_in_cut * 60))
        tx = (rng2.random() - 0.5) * amp * 30
        ty = (rng2.random() - 0.5) * amp * 30
        rot = (rng2.random() - 0.5) * amp * 3
        M = affine(1.0 + amp * 0.05, tx, ty, rot)
    elif motion == "rotate_cw":
        M = affine(1.0 + amp * 0.1, 0, 0,  amp * 12 * p)
    elif motion == "rotate_ccw":
        M = affine(1.0 + amp * 0.1, 0, 0, -amp * 12 * p)
    elif motion == "ken_burns":
        # smooth combined zoom + pan in a random direction
        ang = rng.uniform(0, 6.283)
        scale = 1.0 + amp * 0.30 * p
        tx = math.cos(ang) * amp * w * 0.10 * p
        ty = math.sin(ang) * amp * h * 0.10 * p
        M = affine(scale, tx, ty, 0)
    elif motion == "glitch_jump":
        # discrete jumps every ~3 frames at 30 fps
        step = int(t_in_cut * 10) + seed
        rng2 = random.Random(step)
        tx = (rng2.random() - 0.5) * amp * w * 0.08
        ty = (rng2.random() - 0.5) * amp * h * 0.08
        M = affine(1.0 + amp * 0.05, tx, ty, 0)
    else:
        return frame

    return cv2.warpAffine(frame, M, (w, h),
                          borderMode=cv2.BORDER_REFLECT_101)


def overlay_pip(base, small_src, t_in_cut, cut_duration, seed):
    """Drops a scaled-down rectangle of `small_src` onto `base` at a position
    chosen from `seed`. Used when Cut.overlay is True."""
    h, w = base.shape[:2]
    rng = random.Random(seed)
    scale = rng.uniform(0.30, 0.55)
    iw = int(w * scale); ih = int(h * scale)
    if iw < 8 or ih < 8:
        return base
    x0 = rng.randint(0, max(0, w - iw))
    y0 = rng.randint(0, max(0, h - ih))
    small = cv2.resize(small_src, (iw, ih), interpolation=cv2.INTER_AREA)
    out = base.copy()
    # 1-px accent border so the overlay reads as intentional
    out[y0:y0 + ih, x0:x0 + iw] = small
    cv2.rectangle(out, (x0, y0), (x0 + iw - 1, y0 + ih - 1),
                  (220, 220, 220), 1)
    return out


def _blend_params(p1, p2, alpha: float):
    """Linearly interpolate every numeric field of two Params snapshots,
    plus crossfade per-effect mix. Returns a new Params-like object."""
    from copy import copy
    from dataclasses import fields
    out = copy(p1)
    a = _smoothstep(alpha)         # ease in/out for nicer motion
    for f in fields(p1):
        v1 = getattr(p1, f.name)
        v2 = getattr(p2, f.name)
        if isinstance(v1, (int, float)) and not isinstance(v1, bool) \
                and isinstance(v2, (int, float)):
            mixed = v1 * (1.0 - a) + v2 * a
            if isinstance(v1, int):
                mixed = int(round(mixed))
            setattr(out, f.name, mixed)
    # effects: lerp mix on the SAME kinds, take p2's enabled-set as we cross over
    by_kind = {e.kind: e for e in p2.effects}
    out.effects = []
    for e1 in p1.effects:
        e2 = by_kind.get(e1.kind, e1)
        ne = copy(e1)
        ne.mix = float(e1.mix) * (1.0 - a) + float(e2.mix) * a
        # toggle handover: after 50% fade we adopt p2's enabled flag
        ne.enabled = e2.enabled if a > 0.5 else e1.enabled
        out.effects.append(ne)
    return out


def _enable_beat_intensive_preset(state: SharedState, seed: int,
                                   overrides: dict | None = None) -> None:
    """Set up a tasteful base look + datamosh-leaning accents.

    Base = luma_key + grain + soft trails + a flat CRT overlay (no curvature)
    that gives the footage scanlines/triad texture without distorting shape.
    Saturation is capped to 60% so colours stay natural.

    `overrides` is a dict keyed by effect kind:
        {"state": "auto"|"on"|"off"|"random",
         "mix":   float 0..1,
         "random_mix": bool}
    "auto"   — no override (preset+smart_random decide)
    "on"     — force enabled; use mix value (or roll it if random_mix)
    "off"    — force disabled
    "random" — 50/50 per cut; mix follows same rule
    """
    base_on = {"luma_key", "heavy_grain", "motion_trails", "crt_monitor"}
    accent_probs = {
        "datamosh":    0.65,
        "wave_warp":   0.18,
        "pixel_sort":  0.12,
        "vhs":         0.10,
    }
    rng = random.Random(seed)
    for i, eff in enumerate(state.params.effects):
        if eff.kind in base_on:
            state.update_effect(i, enabled=True, mix=rng.uniform(0.45, 0.70))
        elif eff.kind == "dual_tone":
            state.update_effect(i, enabled=True, mix=0.85)
        elif eff.kind in accent_probs:
            p = accent_probs[eff.kind]
            state.update_effect(i, enabled=rng.random() < p,
                                mix=rng.uniform(0.50, 0.85))
        else:
            state.update_effect(i, enabled=False)
    smart_random(state, seed=seed ^ 0xA53C7)

    # Non-negotiable stamp: flat CRT monitor + saturation cap.
    state.update_param("crtm_curvature",  0.0)
    state.update_param("crtm_scanline",   0.35)
    state.update_param("crtm_grille",     0.15)
    state.update_param("crtm_vignette",   0.10)
    state.update_param("crtm_bloom",      0.0)
    state.update_param("crtm_aberration", 0.0)
    state.update_param("saturation",      0.60)

    # Apply user overrides on top of everything else.
    if not overrides:
        return
    ov_rng = random.Random(seed ^ 0xBEEF5)
    for i, eff in enumerate(state.params.effects):
        ov = overrides.get(eff.kind)
        if not ov:
            continue
        # compositing mode is independent of the enable/mix state, so apply it
        # even for "auto" effects (lets the user re-blend a preset-rolled effect).
        if "blend" in ov:
            state.update_effect(i, blend=float(ov.get("blend", 0.0)))
        mode = ov.get("state", "auto")
        if mode == "auto":
            continue
        if mode == "off":
            state.update_effect(i, enabled=False)
            continue
        # "on" or "random"
        if mode == "random":
            enabled = ov_rng.random() < 0.5
        else:
            enabled = True
        mix_val = float(ov.get("mix", 0.65))
        if ov.get("random_mix", False):
            mix_val = ov_rng.uniform(0.30, 0.90)
        state.update_effect(i, enabled=enabled, mix=mix_val)


def _flash_effects(state: SharedState, intensity: float) -> None:
    """Apply a brief flash boost on a few accent effects (0..1 intensity)."""
    boost_mix = {
        "datamosh":   0.85 * intensity,
        "pixel_sort": 0.75 * intensity,
        "wave_warp":  0.60 * intensity,
    }
    for i, eff in enumerate(state.params.effects):
        if eff.kind in boost_mix and eff.enabled:
            state.update_effect(i, mix=boost_mix[eff.kind])


def _mux_audio(video_path: Path, audio_path: Path, output: Path,
               offset: float, duration: float) -> bool:
    """Combine video + audio using ffmpeg. Re-uses imageio_ffmpeg's binary."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("[automatch] ffmpeg not found; output is silent")
        try:
            shutil.copy(video_path, output)
        except Exception:
            pass
        return False
    args = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-ss", str(offset), "-t", str(duration),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        # strip ALL metadata/chapters from both inputs, suppress encoder tags,
        # and move the moov atom to the front for fast upload/streaming.
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-flags:a", "+bitexact", "-fflags", "+bitexact",
        "-empty_hdlr_name", "1",
        "-movflags", "+faststart",
        str(output),
    ]
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        print(f"[automatch] ffmpeg mux failed: {proc.stderr.decode(errors='ignore')[:300]}")
        return False
    return True


def audio_duration(path: Path) -> Optional[float]:
    """Return total length of an audio file in seconds, or None if unknown."""
    path = Path(path)
    # Try librosa first if installed
    try:
        import librosa  # type: ignore
        return float(librosa.get_duration(path=str(path)))
    except Exception:
        pass
    # ffmpeg fallback — parse its stderr for "Duration: HH:MM:SS.MS"
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    proc = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True)
    text = proc.stderr.decode("utf-8", errors="ignore")
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if m:
        h, mn, s = m.groups()
        return float(h) * 3600 + float(mn) * 60 + float(s)
    return None


def _clamp_audio_offset(audio_path: Path, settings) -> float:
    """Guard against a start offset that lands past the end of the track.

    A stale offset (e.g. carried over from a longer track) makes ffmpeg seek
    past EOF and return 0 samples — silent preview — and makes librosa analyse
    an empty region and fall back. Clamp it into range so the last `duration`
    seconds play instead. Mutates settings so analysis + playback agree.
    """
    total = audio_duration(audio_path)
    off = float(getattr(settings, "audio_offset_sec", 0.0))
    if total is not None and off >= total:
        new_off = max(0.0, total - float(settings.duration))
        print(f"[audio] offset {off:.1f}s is past end of track "
              f"({total:.1f}s) — clamping to {new_off:.1f}s")
        settings.audio_offset_sec = new_off
        return new_off
    return off


def _load_audio_array(path: Path, offset: float, duration: float,
                      sr: int = 44100):
    """Decode audio to a (samples, 2) float32 array via ffmpeg."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("[audio] no ffmpeg found (imageio_ffmpeg / PATH) — preview will be silent")
        return None, sr
    args = [
        ffmpeg, "-y", "-v", "error",
        "-ss", str(offset), "-t", str(duration),
        "-i", str(path),
        "-ac", "2", "-ar", str(sr), "-f", "f32le", "pipe:1",
    ]
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[:300]
        print(f"[audio] ffmpeg decode failed (rc={proc.returncode}): {err or '<no stderr>'}")
        return None, sr
    raw = np.frombuffer(proc.stdout, dtype=np.float32)
    if raw.size == 0:
        print(f"[audio] ffmpeg produced 0 samples for {Path(path).name}")
        return None, sr
    return raw.reshape(-1, 2), sr


def _apply_effect_chain(snap, chain: list) -> None:
    """Reorder snap.effects so enabled effects follow the user's chain order.

    Effects in `chain` come first (in chain order), remaining effects follow
    in their original order.  Disabled effects are kept — the renderer skips
    them naturally.
    """
    by_kind = {e.kind: e for e in snap.effects}
    seen = set()
    ordered = []
    for kind in chain:
        if kind in by_kind and kind not in seen:
            ordered.append(by_kind[kind])
            seen.add(kind)
    for e in snap.effects:
        if e.kind not in seen:
            ordered.append(e)
    snap.effects = ordered


def preview(audio_path: Path, media_folder: Path,
            settings: AutoEditSettings,
            progress: Optional[Callable[[float, str], None]] = None) -> None:
    """Run the same edit logic but draw to a GLFW window and loop the audio.
    Close the window (or press Esc) to exit."""
    import glfw

    audio_path = Path(audio_path)
    media_folder = Path(media_folder)

    def report(p, msg):
        if progress is not None:
            try: progress(p, msg)
            except Exception: pass
        print(f"[preview] {msg}")

    _clamp_audio_offset(audio_path, settings)
    report(0.05, "analysing audio")
    audio = analyze_audio_full(audio_path,
                                offset=settings.audio_offset_sec,
                                duration=settings.duration,
                                override_bpm=getattr(settings, "override_bpm", 0.0))
    bpm, beats = audio.bpm, audio.beats
    report(0.15, f"BPM ≈ {bpm:.1f}, {len(beats)} beats")

    report(0.20, "loading media")
    pool = MediaPool(media_folder, stabilize=settings.cam_stabilize,
                     single_video_clips=settings.single_video_clips,
                     single_video_seed=int(settings.seed) or 0)
    types = [("img" if p.suffix.lower() in MediaPool.IMAGE_EXT else "vid")
             for p in pool.paths]
    report(0.22, f"{len(pool)} files: {dict((t, types.count(t)) for t in set(types))}")

    # bypass_montage: skip beat-sync cutting, treat the whole duration as one cut.
    if getattr(settings, "bypass_montage", False):
        # Single long cut — no beat-sync editing, effects applied to full footage.
        cuts = [Cut(
            t_start=0.0,
            t_end=settings.duration,
            clip_idx=0,
            preset_seed=int(settings.seed) or 1,
        )]
        report(0.22, "bypass_montage: single-cut mode")
    else:
        cuts = build_timeline(beats, settings, len(pool), seed=settings.seed,
                              get_duration=pool.get_duration,
                              audio=audio)
    clip_seq = [c.clip_idx for c in cuts]
    report(0.30, f"{len(pool)} clips, {len(cuts)} cuts (seed={settings.seed})")
    report(0.31, f"clip sequence: {clip_seq}")

    # GLFW window + GL context
    if not glfw.init():
        raise RuntimeError("glfw.init() failed")
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.RESIZABLE, glfw.TRUE)
    # preview uses half-res internal pipeline so the heavy CPU bits (motion
    # warp, transition blends, frame reads) are 4× cheaper; the GLFW window
    # itself stays full-size and GL scales the final blit
    # preview resolution is scaled by settings.preview_scale (default 0.5).
    # Lower it for laggy previews; render is always full-res.
    _pv_scale = max(0.10, min(1.0, getattr(settings, "preview_scale", 0.5)))
    # Resolve aspect preset → effective output_w/h (overrides slider values
    # unless aspect_mode == "custom").
    ow_full, oh_full = resolve_aspect(settings)
    pw = max(256, int(ow_full * _pv_scale))
    ph = max(144, int(oh_full * _pv_scale))
    win = glfw.create_window(ow_full, oh_full,
                             "AutoMatch preview (esc to close)", None, None)
    if not win:
        glfw.terminate()
        raise RuntimeError("glfw.create_window() failed")
    glfw.make_context_current(win)
    glfw.swap_interval(1)
    ctx = moderngl.create_context()

    rdr = OffscreenRenderer(pw, ph, ctx=ctx)

    state = SharedState()
    state.update_param("width",  settings.output_w)
    state.update_param("height", settings.output_h)

    # Pre-compute one snapshot per cut so we don't smart_random in the hot loop
    cut_snapshots: list = []
    for cut in cuts:
        _enable_beat_intensive_preset(state, cut.preset_seed,
                                      overrides=settings.effect_overrides)
        apply_palette(state, settings.palette, settings.palette_strength)
        # Apply user dither overrides AFTER smart_random / palette so they win.
        for k, v in (settings.dither_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # Same for ASCII overrides (cell_size, invert, color_source, tint).
        for k, v in (settings.ascii_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # Same for Pixel sort overrides (algorithm, threshold, length, color).
        for k, v in (settings.psort_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # Kaleidoscope + slit-scan overrides (same field-name pattern).
        for k, v in (settings.kaleido_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        for k, v in (settings.slit_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        for k, v in (settings.distort_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # New effects — thermal / cellular / elastic_grid / crt_monitor /
        # glitch / modulate (same field-name → value override pattern).
        for _ov in (settings.thermal_params, settings.cellular_params,
                    settings.elastic_grid_params, settings.crt_monitor_params,
                    settings.glitch_params, settings.modulate_params,
                    settings.pixelate_params, settings.extra_fx_params):
            for k, v in (_ov or {}).items():
                try:
                    state.update_param(k, float(v))
                except Exception:
                    pass
        # User-typed ASCII glyph string (string, not a numeric param) —
        # propagated into the snapshot so the renderer can re-bake the atlas.
        snap = state.snapshot_params()
        if settings.ascii_text:
            try:
                snap.ascii_text = settings.ascii_text
            except Exception:
                pass
        # Reorder effects according to user-defined chain.
        if settings.effect_chain:
            _apply_effect_chain(snap, settings.effect_chain)
        cut_snapshots.append(snap)

    if cut_snapshots:
        s0 = cut_snapshots[0]
        pal_resolved = _resolve_palette(settings.palette)
        report(0.35,
            f"palette={settings.palette!r} resolved={pal_resolved is not None} "
            f"dual_shadow=({s0.dual_shadow_r:.2f},{s0.dual_shadow_g:.2f},{s0.dual_shadow_b:.2f}) "
            f"dual_high=({s0.dual_high_r:.2f},{s0.dual_high_g:.2f},{s0.dual_high_b:.2f})")

    # load and start audio
    try:
        import sounddevice as sd
        audio_arr, sr = _load_audio_array(audio_path,
                                          settings.audio_offset_sec,
                                          settings.duration)
        sound_ok = audio_arr is not None
        if sound_ok:
            sd.play(audio_arr, samplerate=sr, loop=False)
            print(f"[preview] audio playing: {len(audio_arr)} samples @ {sr} Hz "
                  f"(device: {sd.query_devices(sd.default.device[1])['name']})")
        else:
            print("[preview] no audio array — preview is silent (see [audio] log above)")
    except Exception as e:
        print(f"[preview] audio failed: {type(e).__name__}: {e}")
        sound_ok = False

    # ESC to quit
    def on_key(_w, key, _sc, action, _mods):
        if key == glfw.KEY_ESCAPE and action == glfw.PRESS:
            glfw.set_window_should_close(_w, True)
    glfw.set_key_callback(win, on_key)

    import time
    last_audio_restart = time.monotonic()
    # Per-pass blob tracker state (active only when tracker_mode != "hsv")
    blob_tracker_state = _BlobTrackerState(mode=settings.blob_track.tracker_mode)
    _last_cut_idx = -1   # for feedback-buffer reset on cut boundaries
    mod_matrix = ModMatrix(getattr(settings, "mod_config", None))  # human modulation

    while not glfw.window_should_close(win):
        glfw.poll_events()
        elapsed = time.monotonic() - last_audio_restart
        if elapsed >= settings.duration:
            # loop: restart audio + timing
            try:
                if sound_ok:
                    sd.stop()
                    sd.play(audio_arr, samplerate=sr, loop=False)
            except Exception:
                pass
            last_audio_restart = time.monotonic()
            elapsed = 0.0
            _last_cut_idx = -1   # timeline looped → clear feedback next frame
        t = elapsed

        # find active cut
        cur_idx = 0
        for i, cut in enumerate(cuts):
            if cut.t_start <= t < cut.t_end:
                cur_idx = i
                break
        cur = cuts[cur_idx]
        prev = cuts[cur_idx - 1] if cur_idx > 0 else None

        # ── cut boundary → restart frame-recursive effects fresh ──
        # Same rule as export: feedback effects don't bleed across the stitch.
        if cur_idx != _last_cut_idx:
            rdr.reset_feedback()
            _last_cut_idx = cur_idx

        # ---- crossfade fade-in at the start of this cut ----
        fade_dur = settings.crossfade_ms / 1000.0
        fade_alpha = 1.0
        if prev is not None and fade_dur > 0.001:
            dt_in = t - cur.t_start
            if dt_in < fade_dur:
                fade_alpha = max(0.0, min(1.0, dt_in / fade_dur))

        # flash from nearest beat
        flash = 0.0
        last_beat_t = -1.0
        for b in beats:
            if b <= t:
                last_beat_t = float(b)
            else:
                break
        if last_beat_t >= 0.0:
            dt_b = t - last_beat_t
            if 0.0 <= dt_b <= settings.beat_flash_ms / 1000.0:
                flash = 1.0 - (dt_b / (settings.beat_flash_ms / 1000.0))

        # blend params: previous snapshot → current snapshot over fade
        cur_snap = cut_snapshots[cur_idx]
        if prev is not None and fade_alpha < 1.0:
            prev_snap = cut_snapshots[cur_idx - 1]
            params = _blend_params(prev_snap, cur_snap, fade_alpha)
        else:
            from copy import copy as _c
            params = _c(cur_snap)
            params.effects = [_c(e) for e in cur_snap.effects]

        # apply beat flash
        # ── energy-aware flash boost ──
        # cut.energy modulates how aggressive the on-beat surge is;
        # drops push it harder; downbeats get a bonus too.
        boost = 1.0
        boost *= (0.6 + 0.8 * cur.energy)
        if cur.is_drop:       boost *= 1.35
        if cur.is_downbeat:   boost *= 1.10
        boost = min(boost, 1.5)
        for eff in params.effects:
            if eff.enabled and eff.kind in ("datamosh", "pixel_sort", "wave_warp"):
                target_mix = {"datamosh": 0.85, "pixel_sort": 0.75, "wave_warp": 0.60}[eff.kind] * boost
                target_mix = min(target_mix, 1.0)
                eff.mix = max(eff.mix, target_mix * flash + eff.mix * (1.0 - flash))

        # effect-driven transitions: force the bound shader on across the fade,
        # with intensity automated over the 0→1→0 transition arc
        if prev is not None and fade_alpha < 1.0 \
                and cur.transition in EFFECT_TRANSITIONS:
            _apply_effect_transition(params, cur.transition, fade_alpha)

        # ---- current frame (raw — motion handled by GPU when possible) ----
        _remap  = settings.beat_remap_strength
        cur_clip_t = _beat_remap_time(t - cur.t_start, cur.t_start, beats, _remap) \
                     + cur.clip_in_offset
        cur_dur = max(0.001, cur.t_end - cur.t_start)
        try:
            cur_frame_raw = pool.get_frame(cur.clip_idx, cur_clip_t, pw, ph)
        except Exception:
            cur_frame_raw = np.zeros((ph, pw, 3), dtype=np.uint8)
        # Chaos aspect: continuously animate frame size on the canvas.
        chaos_bbox = None
        if settings.aspect_mode == "chaos":
            cur_frame_raw, chaos_bbox = _apply_chaos_frame(cur_frame_raw, t, pw, ph)

        # ---- decide GPU vs CPU path ----------------------------------------
        # GPU warp+crossfade path: common transitions only, no PIP, no blobs
        _in_common_trans = cur.transition in (
            "crossfade", "datamosh_freeze",
            "pixel_sort_sweep", "rgb_split_glitch", "vhs_burst")
        _has_transition  = prev is not None and fade_alpha < 1.0
        _use_gpu         = (
            _has_transition
            and _in_common_trans
            and not cur.overlay
            and not settings.blob_track.enabled
        )
        # Text cards replace the frame — GPU warp not useful, disabled below

        if _use_gpu:
            prev_clip_t = _beat_remap_time(t - prev.t_start, prev.t_start, beats, _remap) \
                          + prev.clip_in_offset
            prev_dur    = max(0.001, prev.t_end - prev.t_start)
            try:
                prev_frame_raw = pool.get_frame(prev.clip_idx, prev_clip_t, pw, ph)
            except Exception:
                prev_frame_raw = np.zeros((ph, pw, 3), dtype=np.uint8)
            if settings.aspect_mode == "chaos":
                prev_frame_raw, _ = _apply_chaos_frame(prev_frame_raw, t, pw, ph)

            # GPU-side fade alpha for datamosh_freeze:
            # 0..0.65 → hold on prev (fade=0), 0.65..1 → dissolve
            if cur.transition == "datamosh_freeze":
                # brief hold on prev (~35% of the fade) then dissolve through —
                # enough to read as a freeze without feeling like a lag/stutter
                gpu_fade = 0.0 if fade_alpha < 0.35 else (fade_alpha - 0.35) / 0.65
            else:
                gpu_fade = fade_alpha

            warp_cur_m  = _build_affine_mat(
                cur.motion,  cur.motion_amp,  cur_clip_t,  cur_dur,
                cur.preset_seed,  pw, ph)
            warp_prev_m = _build_affine_mat(
                prev.motion, prev.motion_amp, prev_clip_t, prev_dur,
                prev.preset_seed, pw, ph)

            frame      = cur_frame_raw      # sent as cur_frame_bgr
            prev_frame = prev_frame_raw     # sent as prev_frame_bgr

        else:
            # CPU fallback: apply_motion + apply_transition as before
            warp_cur_m  = None
            warp_prev_m = None
            gpu_fade    = 1.0
            prev_frame  = None

            frame = apply_motion(cur_frame_raw, cur.motion, cur.motion_amp,
                                 cur_clip_t, cur_dur, cur.preset_seed)

            if _has_transition:
                prev_clip_t = _beat_remap_time(
                    t - prev.t_start, prev.t_start, beats, _remap
                ) + prev.clip_in_offset
                prev_dur    = max(0.001, prev.t_end - prev.t_start)
                try:
                    pf    = pool.get_frame(prev.clip_idx, prev_clip_t, pw, ph)
                    pf    = apply_motion(pf, prev.motion, prev.motion_amp,
                                         prev_clip_t, prev_dur, prev.preset_seed)
                    frame = apply_transition(pf, frame, fade_alpha, cur.transition)
                except Exception:
                    pass

        # ---- PIP overlay (CPU, modifies frame) ----
        if cur.overlay and cur_idx + 1 < len(cuts):
            nxt = cuts[cur_idx + 1]
            try:
                nxt_clip_t = (t - cur.t_start) * 0.5
                nxt_frame  = pool.get_frame(nxt.clip_idx, nxt_clip_t, pw, ph)
                frame = overlay_pip(frame, nxt_frame, cur_clip_t, cur_dur,
                                    cur.preset_seed)
            except Exception:
                pass

        # ---- blob tracking (CPU) ----
        bt = settings.blob_track
        if bt.enabled:
            blobs = _track_blobs(frame, bt, blob_tracker_state)
            if blobs and bt.zoom_to_blob:
                cx_n = blobs[0][0] / max(1, pw)
                cy_n = blobs[0][1] / max(1, ph)
                frame = _blob_zoom_frame(frame, cx_n, cy_n,
                                         cur_clip_t, cur_dur, cur.motion_amp)
            if bt.show_overlay:
                frame = _draw_blob_overlay(frame, blobs, bt, blob_tracker_state,
                                           int(t * settings.fps))

        # ---- text card flash (replaces frame — disables GPU warp) ----
        if cur.text_card and 0.0 <= (t - cur.t_start) < cur.text_card_dur:
            tc = settings.text_cards
            frame = _render_text_card(
                cur.text_card, pw, ph,
                color=tc.color, bg_color=tc.bg_color,
                bg_alpha=tc.bg_alpha, font_scale=tc.font_scale,
                align=tc.align, font_path=getattr(tc, "font_path", ""),
                base_frame=(frame if tc.bg_alpha < 0.99 else None),
            )
            # text card is a plain CPU frame — no GPU warp needed
            prev_frame  = None
            warp_cur_m  = None
            gpu_fade    = 1.0

        # ---- first-beat ASCII logo flash (easter egg, composited on top) ----
        _la = _logo_flash_alpha(t, beats, settings)

        # ── per-frame human modulation (LFOs + audio envelopes → params) ──
        mod_matrix.apply(params, t, audio)

        fb_w, fb_h = glfw.get_framebuffer_size(win)
        rdr.render(frame, params, t,
                   prev_frame_bgr=prev_frame,
                   warp_cur=warp_cur_m, warp_prev=warp_prev_m,
                   fade_in=gpu_fade,
                   present_screen_size=(fb_w, fb_h),
                   chaos_bbox=chaos_bbox,
                   logo_alpha=_la)
        glfw.swap_buffers(win)

    # cleanup
    try:
        if sound_ok:
            import sounddevice as sd
            sd.stop()
    except Exception:
        pass
    pool.close()
    ctx.release()
    glfw.destroy_window(win)
    glfw.terminate()
    report(1.0, "preview closed")


def run(audio_path: Path, media_folder: Path, output_path: Path,
        settings: AutoEditSettings,
        progress: Optional[Callable[[float, str], None]] = None,
        cancel: Optional["threading.Event"] = None) -> Path:
    """Render the auto-edit. Returns the path to the produced mp4.

    `cancel` is an optional threading.Event — when set, the render aborts:
        - the per-frame loop breaks at the next iteration
        - the ffmpeg subprocess is terminated
        - any partially-written .silent.mp4 is removed
        - a RuntimeError("render cancelled") is raised
    """
    audio_path = Path(audio_path)
    media_folder = Path(media_folder)
    output_path = Path(output_path)

    def report(p, msg):
        if progress is not None:
            try: progress(p, msg)
            except Exception: pass
        print(f"[automatch] {msg}")

    _clamp_audio_offset(audio_path, settings)
    report(0.02, f"analysing audio: {audio_path.name}")
    audio = analyze_audio_full(audio_path,
                                offset=settings.audio_offset_sec,
                                duration=settings.duration,
                                override_bpm=getattr(settings, "override_bpm", 0.0))
    bpm, beats = audio.bpm, audio.beats
    report(0.08, f"BPM ≈ {bpm:.1f}, beats={len(beats)}")

    report(0.10, f"loading media from {media_folder}")
    pool = MediaPool(media_folder, stabilize=settings.cam_stabilize,
                     single_video_clips=settings.single_video_clips,
                     single_video_seed=int(settings.seed) or 0)
    types = [("img" if p.suffix.lower() in MediaPool.IMAGE_EXT else "vid")
             for p in pool.paths]
    report(0.12, f"{len(pool)} files: {dict((t, types.count(t)) for t in set(types))}")
    for i, (p, k) in enumerate(zip(pool.paths, types)):
        report(0.13, f"  [{i}] {k}  {p.name}")

    cuts = build_timeline(beats, settings, len(pool), seed=settings.seed,
                          get_duration=pool.get_duration,
                          audio=audio)
    clip_seq = [c.clip_idx for c in cuts]
    report(0.15, f"timeline: {len(cuts)} cuts (seed={settings.seed})")
    report(0.16, f"clip sequence: {clip_seq}")

    # set up state
    state = SharedState()
    # Resolve aspect preset → effective render output_w/h
    ow_render, oh_render = resolve_aspect(settings)
    state.update_param("width",  ow_render)
    state.update_param("height", oh_render)
    report(0.17, f"aspect {settings.aspect_mode!r} → {ow_render}×{oh_render}")

    # GL renderer
    report(0.18, "initialising headless renderer")
    rdr = OffscreenRenderer(ow_render, oh_render)

    # ffmpeg writer
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_video = output_path.with_suffix(".silent.mp4")
    enc_args = [
        ffmpeg, "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{ow_render}x{oh_render}",
        "-r", str(settings.fps),
        "-i", "pipe:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", settings.ffmpeg_preset, "-crf", str(settings.crf),
        # clean export: suppress x264 version SEI + ffmpeg creation tags so the
        # copied stream carries no metadata downstream (TikTok-friendly).
        "-x264-params", "no-info=1",
        "-flags:v", "+bitexact", "-fflags", "+bitexact",
        str(tmp_video),
    ]
    enc = subprocess.Popen(enc_args, stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)

    total_frames = int(settings.duration * settings.fps)
    # Per-render-pass blob tracker state (only used when tracker_mode != "hsv")
    blob_tracker_state = _BlobTrackerState(mode=settings.blob_track.tracker_mode)
    # Pre-compute one snapshot per cut (so we don't smart_random in the hot
    # loop). Mirror the preview() logic so transitions match exactly.
    cut_snapshots: list = []
    for cut in cuts:
        _enable_beat_intensive_preset(state, cut.preset_seed,
                                      overrides=settings.effect_overrides)
        apply_palette(state, settings.palette, settings.palette_strength)
        # Apply user dither overrides AFTER smart_random / palette so they win.
        for k, v in (settings.dither_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # Same for ASCII overrides (cell_size, invert, color_source, tint).
        for k, v in (settings.ascii_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # Same for Pixel sort overrides (algorithm, threshold, length, color).
        for k, v in (settings.psort_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # Kaleidoscope + slit-scan overrides (same field-name pattern).
        for k, v in (settings.kaleido_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        for k, v in (settings.slit_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        for k, v in (settings.distort_params or {}).items():
            try:
                state.update_param(k, float(v))
            except Exception:
                pass
        # New effects — thermal / cellular / elastic_grid / crt_monitor /
        # glitch / modulate (same field-name → value override pattern).
        for _ov in (settings.thermal_params, settings.cellular_params,
                    settings.elastic_grid_params, settings.crt_monitor_params,
                    settings.glitch_params, settings.modulate_params,
                    settings.pixelate_params, settings.extra_fx_params):
            for k, v in (_ov or {}).items():
                try:
                    state.update_param(k, float(v))
                except Exception:
                    pass
        # User-typed ASCII glyph string (string, not a numeric param) —
        # propagated into the snapshot so the renderer can re-bake the atlas.
        snap = state.snapshot_params()
        if settings.ascii_text:
            try:
                snap.ascii_text = settings.ascii_text
            except Exception:
                pass
        if settings.effect_chain:
            _apply_effect_chain(snap, settings.effect_chain)
        cut_snapshots.append(snap)

    # diagnostic: confirm palette actually landed in the first snapshot
    if cut_snapshots:
        s0 = cut_snapshots[0]
        pal_resolved = _resolve_palette(settings.palette)
        report(0.17,
            f"palette={settings.palette!r} resolved={pal_resolved is not None} "
            f"dual_shadow=({s0.dual_shadow_r:.2f},{s0.dual_shadow_g:.2f},{s0.dual_shadow_b:.2f}) "
            f"dual_high=({s0.dual_high_r:.2f},{s0.dual_high_g:.2f},{s0.dual_high_b:.2f})")
        # also verify dual_tone is actually enabled in the pipeline
        dt = next((e for e in s0.effects if e.kind == "dual_tone"), None)
        if dt is not None:
            report(0.18, f"dual_tone enabled={dt.enabled} mix={dt.mix:.2f}")

    beat_set = list(beats)
    fade_dur = settings.crossfade_ms / 1000.0
    _last_cut_idx = -1   # for feedback-buffer reset on cut boundaries
    mod_matrix = ModMatrix(getattr(settings, "mod_config", None))  # human modulation

    for f in range(total_frames):
        # ── cancellation check ──
        if cancel is not None and cancel.is_set():
            try:
                enc.stdin.close()
            except Exception:
                pass
            try:
                enc.terminate()
                enc.wait(timeout=2.0)
            except Exception:
                pass
            try:
                tmp_video.unlink(missing_ok=True)
            except Exception:
                pass
            pool.close()
            raise RuntimeError("render cancelled")
        t = f / settings.fps

        # locate active cut
        cur_idx = 0
        for i, cut in enumerate(cuts):
            if cut.t_start <= t < cut.t_end:
                cur_idx = i
                break
        cur = cuts[cur_idx]
        prev = cuts[cur_idx - 1] if cur_idx > 0 else None

        # ── cut boundary → restart frame-recursive effects fresh ──
        # Feedback effects (motion_trails / datamosh / slit_scan / cellular /
        # glitch) must NOT carry state across the stitch: each clip's effect is
        # built per-frame, then frames are concatenated — not the reverse.
        if cur_idx != _last_cut_idx:
            rdr.reset_feedback()
            _last_cut_idx = cur_idx

        # fade alpha at the start of this cut
        fade_alpha = 1.0
        if prev is not None and fade_dur > 0.001:
            dt_in = t - cur.t_start
            if dt_in < fade_dur:
                fade_alpha = max(0.0, min(1.0, dt_in / fade_dur))

        # blend params
        cur_snap = cut_snapshots[cur_idx]
        if prev is not None and fade_alpha < 1.0:
            prev_snap = cut_snapshots[cur_idx - 1]
            params = _blend_params(prev_snap, cur_snap, fade_alpha)
        else:
            from copy import copy as _c
            params = _c(cur_snap)
            params.effects = [_c(e) for e in cur_snap.effects]

        # beat flash intensity
        flash = 0.0
        last_beat_t = -1.0
        for b in beat_set:
            if b <= t:
                last_beat_t = float(b)
            else:
                break
        if last_beat_t >= 0.0:
            dt_b = t - last_beat_t
            if 0.0 <= dt_b <= settings.beat_flash_ms / 1000.0:
                flash = 1.0 - (dt_b / (settings.beat_flash_ms / 1000.0))
        # ── energy-aware flash boost ──
        # cut.energy modulates how aggressive the on-beat surge is;
        # drops push it harder; downbeats get a bonus too.
        boost = 1.0
        boost *= (0.6 + 0.8 * cur.energy)
        if cur.is_drop:       boost *= 1.35
        if cur.is_downbeat:   boost *= 1.10
        boost = min(boost, 1.5)
        for eff in params.effects:
            if eff.enabled and eff.kind in ("datamosh", "pixel_sort", "wave_warp"):
                target_mix = {"datamosh": 0.85, "pixel_sort": 0.75, "wave_warp": 0.60}[eff.kind] * boost
                target_mix = min(target_mix, 1.0)
                eff.mix = max(eff.mix, target_mix * flash + eff.mix * (1.0 - flash))

        # effect-driven transitions: force the bound shader on across the fade,
        # with intensity automated over the 0→1→0 transition arc
        if prev is not None and fade_alpha < 1.0 \
                and cur.transition in EFFECT_TRANSITIONS:
            _apply_effect_transition(params, cur.transition, fade_alpha)

        # ---- current frame (raw — motion handled by GPU when possible) ----
        _remap     = settings.beat_remap_strength
        cur_clip_t = _beat_remap_time(t - cur.t_start, cur.t_start, beats, _remap) \
                     + cur.clip_in_offset
        cur_dur    = max(0.001, cur.t_end - cur.t_start)
        ow, oh     = resolve_aspect(settings)
        try:
            cur_frame_raw = pool.get_frame(cur.clip_idx, cur_clip_t, ow, oh)
        except Exception:
            cur_frame_raw = np.zeros((oh, ow, 3), dtype=np.uint8)
        chaos_bbox = None
        if settings.aspect_mode == "chaos":
            cur_frame_raw, chaos_bbox = _apply_chaos_frame(cur_frame_raw, t, ow, oh)

        # ---- decide GPU vs CPU path ----------------------------------------
        _in_common_trans = cur.transition in (
            "crossfade", "datamosh_freeze",
            "pixel_sort_sweep", "rgb_split_glitch", "vhs_burst")
        _has_transition  = prev is not None and fade_alpha < 1.0
        _use_gpu         = (
            _has_transition
            and _in_common_trans
            and not cur.overlay
            and not settings.blob_track.enabled
        )

        if _use_gpu:
            prev_clip_t = _beat_remap_time(
                t - prev.t_start, prev.t_start, beats, _remap)
            prev_dur    = max(0.001, prev.t_end - prev.t_start)
            try:
                prev_frame_raw = pool.get_frame(prev.clip_idx, prev_clip_t, ow, oh)
            except Exception:
                prev_frame_raw = np.zeros((oh, ow, 3), dtype=np.uint8)
            if settings.aspect_mode == "chaos":
                prev_frame_raw, _ = _apply_chaos_frame(prev_frame_raw, t, ow, oh)

            if cur.transition == "datamosh_freeze":
                # brief hold on prev (~35% of the fade) then dissolve through —
                # enough to read as a freeze without feeling like a lag/stutter
                gpu_fade = 0.0 if fade_alpha < 0.35 else (fade_alpha - 0.35) / 0.65
            else:
                gpu_fade = fade_alpha

            warp_cur_m  = _build_affine_mat(
                cur.motion,  cur.motion_amp,  cur_clip_t,  cur_dur,
                cur.preset_seed,  ow, oh)
            warp_prev_m = _build_affine_mat(
                prev.motion, prev.motion_amp, prev_clip_t, prev_dur,
                prev.preset_seed, ow, oh)

            frame      = cur_frame_raw
            prev_frame = prev_frame_raw

        else:
            warp_cur_m  = None
            warp_prev_m = None
            gpu_fade    = 1.0
            prev_frame  = None

            frame = apply_motion(cur_frame_raw, cur.motion, cur.motion_amp,
                                 cur_clip_t, cur_dur, cur.preset_seed)

            if _has_transition:
                prev_clip_t = _beat_remap_time(
                    t - prev.t_start, prev.t_start, beats, _remap
                ) + prev.clip_in_offset
                prev_dur    = max(0.001, prev.t_end - prev.t_start)
                try:
                    pf    = pool.get_frame(prev.clip_idx, prev_clip_t, ow, oh)
                    pf    = apply_motion(pf, prev.motion, prev.motion_amp,
                                         prev_clip_t, prev_dur, prev.preset_seed)
                    frame = apply_transition(pf, frame, fade_alpha, cur.transition)
                except Exception:
                    pass

        # ---- PIP overlay: drop a small window of the NEXT clip on top ----
        if cur.overlay and cur_idx + 1 < len(cuts):
            nxt = cuts[cur_idx + 1]
            try:
                nxt_clip_t = (t - cur.t_start) * 0.5
                nxt_frame  = pool.get_frame(nxt.clip_idx, nxt_clip_t, ow, oh)
                frame = overlay_pip(frame, nxt_frame, cur_clip_t, cur_dur,
                                    cur.preset_seed)
            except Exception:
                pass

        # ---- blob tracking (CPU) ----
        bt = settings.blob_track
        if bt.enabled:
            blobs = _track_blobs(frame, bt, blob_tracker_state)
            if blobs and bt.zoom_to_blob:
                cx_n = blobs[0][0] / max(1, ow)
                cy_n = blobs[0][1] / max(1, oh)
                frame = _blob_zoom_frame(frame, cx_n, cy_n,
                                         cur_clip_t, cur_dur, cur.motion_amp)
            if bt.show_overlay:
                frame = _draw_blob_overlay(frame, blobs, bt, blob_tracker_state, f)

        # ---- text card flash ----
        if cur.text_card and 0.0 <= (t - cur.t_start) < cur.text_card_dur:
            tc = settings.text_cards
            frame = _render_text_card(
                cur.text_card, ow, oh,
                color=tc.color, bg_color=tc.bg_color,
                bg_alpha=tc.bg_alpha, font_scale=tc.font_scale,
                align=tc.align, font_path=getattr(tc, "font_path", ""),
                base_frame=(frame if tc.bg_alpha < 0.99 else None),
            )
            prev_frame  = None
            warp_cur_m  = None
            gpu_fade    = 1.0

        # ---- first-beat ASCII logo flash (easter egg, composited on top) ----
        _la = _logo_flash_alpha(t, beats, settings)

        # ── per-frame human modulation (LFOs + audio envelopes → params) ──
        mod_matrix.apply(params, t, audio)

        out_bgr = rdr.render(frame, params, t,
                             prev_frame_bgr=prev_frame,
                             warp_cur=warp_cur_m, warp_prev=warp_prev_m,
                             fade_in=gpu_fade,
                             chaos_bbox=chaos_bbox,
                             logo_alpha=_la)
        try:
            enc.stdin.write(out_bgr.tobytes())
        except BrokenPipeError:
            print("[automatch] ffmpeg pipe broken")
            break

        if f % max(1, settings.fps // 2) == 0:
            report(0.20 + 0.75 * (f / total_frames),
                   f"frame {f}/{total_frames}  ({100*f/total_frames:.0f}%)")

    if enc.stdin:
        enc.stdin.close()
    enc.wait(timeout=20)
    pool.close()
    rdr.ctx.release()

    # ── real bitstream datamosh (Avidemux-style) — export-only post-pass ──
    dm_cfg = getattr(settings, "datamosh_render", None)
    video_for_mux = tmp_video
    if dm_cfg is not None and getattr(dm_cfg, "enabled", False):
        report(0.93, "datamosh (bitstream)")
        try:
            import datamosh_bitstream as _dm
            moshed = output_path.with_suffix(".mosh.mp4")
            res = _dm.datamosh_video(
                tmp_video, moshed, dm_cfg,
                fps=settings.fps, duration=settings.duration,
                seed=int(settings.seed) or 0,
                crf=settings.crf, preset=settings.ffmpeg_preset,
                out_w=ow_render, out_h=oh_render,
                report=lambda m: report(0.94, m),
            )
            if res is not None:
                video_for_mux = res
            else:
                report(0.94, "datamosh skipped (fell back to clean video)")
        except Exception as e:
            print(f"[automatch] datamosh post-process failed: {e}")

    report(0.95, "muxing audio")
    _mux_audio(video_for_mux, audio_path, output_path,
               settings.audio_offset_sec, settings.duration)
    try:
        tmp_video.unlink(missing_ok=True)
        if video_for_mux != tmp_video:
            video_for_mux.unlink(missing_ok=True)
    except Exception:
        pass
    report(1.00, f"done → {output_path}")
    return output_path
