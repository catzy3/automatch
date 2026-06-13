"""Shared parameter state between GUI, capture and render threads."""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import List


EFFECT_KINDS = (
    # cyber-vision aesthetic:
    "luma_key", "motion_trails", "heavy_grain",
    "duotone", "cctv_grid", "quad_split", "hud_overlay",
    # depth-based masking:
    "depth_save", "depth_apply", "depth_view",
    # maximalist / dreamcore:
    "bloom_glow", "dual_tone", "fragment_layers",
    # horizontal-smear / wave-warp:
    "pixel_sort", "wave_warp", "ascii_mosaic",
    # analog distortions:
    "datamosh", "pixel_swap", "mirror_flow", "vhs",
    "rgb_split", "crt_monitor", "drip", "glitch", "dither",
    "edges",
)


@dataclass
class EffectConfig:
    kind: str
    enabled: bool = True
    mix: float = 1.0
    # Compositing mode for the effect's wet result over its dry input, à la a
    # TouchDesigner Composite TOP. 0 = Cross/Over (linear mix — the historical
    # behaviour). Shaders that declare `u_blend` honour the rest; others ignore.
    #   0 cross  1 add  2 screen  3 multiply  4 overlay
    #   5 difference  6 lighten(max)  7 darken(min)  8 dodge  9 burn  10 subtract
    blend: float = 0.0


def default_pipeline() -> List[EffectConfig]:
    return [
        EffectConfig("luma_key",        True,  0.85),
        EffectConfig("motion_trails",   True,  0.55),
        EffectConfig("pixel_sort",      False, 0.65),
        EffectConfig("wave_warp",       False, 0.55),
        EffectConfig("ascii_mosaic",    False, 0.80),
        EffectConfig("bloom_glow",      False, 0.65),
        EffectConfig("fragment_layers", False, 0.45),
        EffectConfig("hud_overlay",     False, 1.00),
        EffectConfig("cctv_grid",       False, 1.00),
        EffectConfig("quad_split",      False, 1.00),
        EffectConfig("duotone",         False, 0.90),
        # analog (kept available)
        EffectConfig("vhs",             False, 0.55),
        EffectConfig("datamosh",        False, 0.55),
        EffectConfig("pixel_swap",      False, 0.40),
        EffectConfig("mirror_flow",     False, 0.45),
        EffectConfig("rgb_split",       False, 1.00),
        EffectConfig("drip",            False, 0.60),
        EffectConfig("dither",          False, 1.00),
        EffectConfig("edges",           False, 0.80),
        EffectConfig("kaleidoscope",    False, 0.85),
        EffectConfig("slit_scan",       False, 0.85),
        EffectConfig("distort",         False, 0.85),
        EffectConfig("thermal",         False, 0.85),
        EffectConfig("cellular",        False, 0.70),
        EffectConfig("elastic_grid",    False, 0.70),
        EffectConfig("crt_monitor",     False, 1.00),
        # ── new effects (30–31.05 batch) — must be present here or the GUI
        #    grid can't enable them (override loop only touches existing
        #    pipeline entries, it never appends). ──
        EffectConfig("found_footage",         False, 0.70),
        EffectConfig("chromatic_aberration",  False, 0.80),
        EffectConfig("color_posterize",       False, 0.80),
        EffectConfig("swirl",                 False, 0.70),
        EffectConfig("iridescent_foil",       False, 0.70),
        EffectConfig("motion_blur",           False, 0.70),
        EffectConfig("advanced_glitch",       False, 0.65),
    ]


@dataclass
class Params:
    # Source
    device_index: int = 0
    width: int = 1280
    height: int = 720

    # Spout
    sender_name: str = "AnalogVFX"

    # Global
    gain: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0

    # VHS
    vhs_tracking_noise: float = 0.25
    vhs_line_jitter: float = 0.15
    vhs_chroma_blur: float = 0.4
    vhs_color_loss: float = 0.2
    vhs_head_switch: float = 0.35

    # RGB split
    rgb_shift_amount: float = 0.3
    rgb_wave_frequency: float = 8.0
    rgb_wave_speed: float = 1.0

    # Glitch (legacy macroblock)
    glitch_block_size: float = 0.2
    glitch_drop_chance: float = 0.1
    glitch_repeat_chance: float = 0.15

    # CRT (legacy — the original `crt` effect was replaced by `crt_monitor`.
    #  Fields kept so old presets/sessions with crt_* keys still load cleanly;
    #  no live/offline pipeline references them anymore.)
    crt_scanline_strength: float = 0.35
    crt_mask_strength: float = 0.25
    crt_curvature: float = 0.15
    crt_vignette: float = 0.3
    crt_persistence: float = 0.0

    # Drip / feedback
    drip_speed: float = 0.012
    drip_decay: float = 0.94
    drip_mix: float = 0.6

    # Dither (circuit modulation)
    dither_levels:          float = 6.0          # 2..32 quantisation levels
    dither_grid_size:       float = 4.0          # 1..32 px per cell
    dither_osc_a_freq:      float = 15.0         # 0.5..50 spatial freq
    dither_osc_b_freq:      float = 12.0         # 0.5..50 spatial freq
    dither_osc_a_speed:     float = 0.5          # 0..3 temporal speed
    dither_osc_b_speed:     float = 0.3          # 0..3 temporal speed
    dither_mix_mode:        float = 0.0          # 0=avg 1=mul 2=xor 3=cross
    dither_trace_thickness: float = 0.10         # 0..0.5 mask softness
    dither_jitter:          float = 0.10         # 0..1 random noise
    dither_chromatic:       float = 0.0          # 0..1 per-channel phase
    dither_polarity:        float = 0.0          # 0|1 invert pattern
    dither_color_mode:      float = 0.0          # 0=mono 1=channels 2=duotone
    dither_color_a_r:       float = 0.02
    dither_color_a_g:       float = 0.15
    dither_color_a_b:       float = 0.10
    dither_color_b_r:       float = 0.55
    dither_color_b_g:       float = 1.00
    dither_color_b_b:       float = 0.45
    # 0 = circuit modulation (default), 1 = Bayer 4×4, 2 = Bayer 8×8, 3 = blue noise
    dither_algorithm:       float = 0.0

    # Datamosh (motion-vector based)
    datamosh_strength: float = 0.85
    datamosh_freeze: float = 0.45
    datamosh_period: float = 3.5
    datamosh_breakthrough: float = 0.05   # chance of fresh-frame leak inside freeze
    datamosh_jitter: float = 0.30         # pixel-level jitter on warp
    datamosh_chaos: float = 0.40          # per-block strength variance
    datamosh_algorithm: float = 1.0       # 0=legacy P-frame chain, 1=optical-flow mosh

    # Light bleed
    light_threshold: float = 0.55
    light_decay: float = 0.92
    light_spread: float = 0.45
    light_warmth: float = 0.65
    light_smear: float = 0.25
    light_chaos: float = 0.30             # sparkle / hot-spot probability
    light_dance: float = 0.40             # bloom wobble over time
    light_floor: float = 0.10             # always seed a little bloom from mid-tones

    # Block drift
    block_drift_size: float = 0.40
    block_drift_amount: float = 0.55
    block_drift_refresh: float = 0.35
    block_drift_decay: float = 0.97
    block_drift_chaos: float = 0.40       # block-size variance
    block_drift_swap: float = 0.20        # neighbour-block swap chance
    block_drift_warp: float = 0.15        # twist inside each block

    # Pixel swap
    pixel_swap_threshold: float = 0.30    # fraction of cells that swap
    pixel_swap_radius: float = 0.30       # max swap distance
    pixel_swap_chaos: float = 0.50        # magnitude variance
    pixel_swap_directional: float = 0.10  # 0=isotropic, 1=horizontal only
    pixel_swap_temporal: float = 0.40     # 0=static pattern, 1=sizzle per frame

    # Mirror flow
    mirror_drip: float = 0.014
    mirror_decay: float = 0.94
    mirror_unroll: float = 0.85           # overall flip strength
    mirror_axis_h: float = 1.00           # horizontal flip weight
    mirror_axis_v: float = 0.30           # vertical flip weight
    mirror_swirl: float = 0.20            # rotational twist
    mirror_chaos: float = 0.35            # jagged flip boundary

    # ---- cyber-vision effects ----
    # Luma-key (B&W contrast)
    luma_lift: float = 0.00
    luma_gain: float = 1.25
    luma_gamma: float = 0.90
    luma_invert: float = 0.00

    # Duotone (B&W + accent)
    accent_r: float = 1.00            # 0..1
    accent_g: float = 0.42            # 0..1
    accent_b: float = 0.10            # 0..1 — defaults to orange
    duotone_midpoint: float = 0.55
    duotone_strength: float = 0.95

    # Heavy grain
    grain_amount: float = 0.55
    grain_size: float = 0.25
    grain_chroma: float = 0.20
    grain_blotch: float = 0.30

    # Edges
    edges_threshold: float = 0.45
    edges_thickness: float = 0.50
    edges_fade_image: float = 0.40

    # Motion trails — extended
    trails_decay: float = 0.92
    trails_threshold: float = 0.20
    trails_blur: float = 0.45
    trails_drift_x: float = 0.00       # -1..1
    trails_drift_y: float = 0.00       # -1..1
    trails_tint_r: float = 1.00
    trails_tint_g: float = 0.55
    trails_tint_b: float = 0.20
    trails_secondary: float = 0.35     # secondary echo strength
    trails_mode: float = 0.0           # 0..4 = screen/max/add/lighten/overlay
    trails_polarity: float = 0.0       # 0..1 = bright vs dark trails
    trails_chaos: float = 0.20

    # CCTV grid
    cctv_cols: float = 2.0
    cctv_rows: float = 2.0
    cctv_gap: float = 0.008
    cctv_invert_mask: float = 8.0      # bit 3 set → quadrant index 3 inverted
    cctv_delay_mask: float = 0.0       # default all live
    cctv_tint_strength: float = 0.45
    cctv_desat: float = 0.30
    cctv_label_brightness: float = 1.0

    # Quad split
    quad_gap: float = 0.005
    quad_invert_mask: float = 8.0     # bitmask 0..15 (default = quad 3 inverted)
    quad_freeze: float = 0.85

    # HUD overlay (drawn CPU-side)
    hud_brightness: float = 1.5
    hud_show_face_box: bool = True
    hud_show_face_keypoints: bool = True
    hud_show_face_mesh: bool = False
    hud_show_pose: bool = True
    hud_show_vectors: bool = True
    hud_show_text: bool = True
    hud_show_crosshairs: bool = True
    hud_line_thickness: int = 2

    # Detection sensitivity & enable flags
    detect_min_confidence: float = 0.55
    detect_eye_sf: float = 1.10
    detect_eye_neighbors: int = 5
    detect_skip_frames: int = 2

    detect_face: bool = True
    detect_eyes: bool = True
    detect_pose: bool = True
    detect_face_mesh: bool = False
    detect_hands: bool = False              # MediaPipe Hands if available

    # Blob tracking (HSV color-target connected-components)
    detect_blobs: bool = False
    blob_target_r: float = 1.0              # target colour (RGB 0..1)
    blob_target_g: float = 0.2
    blob_target_b: float = 0.2
    blob_hue_tol: float = 12.0              # 0..90 (degrees out of 180)
    blob_sat_min: float = 0.30              # 0..1
    blob_val_min: float = 0.30              # 0..1
    blob_area_min: float = 200.0            # px
    blob_area_max: float = 80000.0
    blob_max_count: int = 8                 # max blobs tracked per frame

    # Depth estimation
    depth_enabled: bool = False             # turn DepthThread on/off

    # HUD: extra toggles for the new tracking layers
    hud_show_eyes: bool = True
    hud_show_pupils: bool = True
    hud_show_hands: bool = True
    hud_show_blobs: bool = True

    # ---- maximalist / dreamcore effects ----
    # Bloom glow (2-pass Gaussian)
    bloom_threshold: float = 0.55
    bloom_radius: float = 0.55
    bloom_intensity: float = 1.20
    bloom_tint_r: float = 1.0
    bloom_tint_g: float = 0.85
    bloom_tint_b: float = 0.55

    # Dual tone (3-stop)
    dual_shadow_r: float = 0.05
    dual_shadow_g: float = 0.30
    dual_shadow_b: float = 0.55
    dual_mid_r: float = 0.85
    dual_mid_g: float = 0.30
    dual_mid_b: float = 0.10
    dual_high_r: float = 1.00
    dual_high_g: float = 0.80
    dual_high_b: float = 0.30
    dual_split: float = 0.60
    dual_strength: float = 0.95

    # Fragment layers
    frag_count: float = 0.45
    frag_height: float = 0.30
    frag_offset: float = 0.30
    frag_alpha: float = 0.65

    # Pixel sort — full customisation
    psort_threshold:    float = 0.00     # 0 = sort whole frame; raise → only bright runs (broken streaks)
    psort_length:       float = 0.40     # РАЗМЕР: sorts 0..1→8..96px, scramble 0..1→4..28px
    psort_direction:    float = 1.00     # -1..+1, 0 = both ways
    psort_band_density: float = 1.00     # 1 = every row/column; lower → striped coverage
    psort_band_jitter:  float = 0.30
    psort_band_speed:   float = 0.50     # 0=slow swap of active bands, 1=fast
    psort_length_var:   float = 0.20     # per-band length jitter
    psort_edge_fade:    float = 0.20     # soft band edges
    psort_color_mode:   float = 0.00     # 0=luma 1=R 2=G 3=B 4=sat
    psort_axis:         float = 0.00     # unused — direction encoded in psort_algorithm
    psort_algorithm:    float = 0.00     # 0=KA horiz 1=KA vert 2=bitonic horiz 3=bitonic vert 4=block scramble
    psort_chromatic:    float = 0.30
    psort_blend:        float = 0.00     # 0=replace 1=screen 2=lighten 3=darken

    # Kaleidoscope — polar wedge fold + mirror (spatial)
    kaleido_sides: float = 6.0    # number of mirror segments (2..16)
    kaleido_angle: float = 0.0    # static rotation offset (turns 0..1)
    kaleido_spin:  float = 0.10   # auto-rotation speed (turns/sec, signed)
    kaleido_zoom:  float = 1.0    # radial zoom (0.3..3; >1 = zoom in)

    # Slit-scan — temporal waterfall time-smear (feedback buffer)
    slit_direction: float = 0.0   # 0 = vertical (rows), 1 = horizontal (columns)
    slit_speed:     float = 0.5   # scroll speed (0..1)
    slit_scanpos:   float = 0.5   # capture line position along scroll axis (0..1)
    slit_feedback:  float = 1.0   # persistence of old lines (1 = none lost)
    slit_threshold: float = 0.0   # luma gate on captured line (0 = off, >0 = cut)

    # Distort — procedural displacement-map warp (tooooools-style)
    #   A noise/wave field is generated in-shader, optionally pre-processed
    #   (blur/grain/gamma/black-white point) the same way you'd tweak a
    #   distortion-map image, then used to offset the source UVs.
    distort_xshift:    float = 0.5   # X displacement strength
    distort_yshift:    float = 0.5   # Y displacement strength
    distort_threshold: float = 0.0   # only displace where map > threshold (0=all)
    distort_scale:     float = 4.0   # procedural map frequency (pattern size)
    distort_speed:     float = 0.3   # map animation speed (0 = frozen)
    distort_blur:      float = 0.0   # soften the map (0..1)
    distort_grain:     float = 0.0   # add fine noise to the map (0..1)
    distort_gamma:     float = 1.0   # map gamma (0.2..4)
    distort_black:     float = 0.0   # map black point (0..1)
    distort_white:     float = 1.0   # map white point (0..1)
    distort_noise_mode: float = 0.0  # 0 = value-noise fBM, 1 = simplex fBM (LYGIA)

    # Thermal — false-colour infrared LUT
    thermal_palette: float = 0.0  # 0=infrared 1=ironbow 2=rainbow 3=arctic
    thermal_gain:    float = 1.0  # pre-multiply luma (0.2..3)
    thermal_gamma:   float = 1.0  # luma gamma (0.2..4)
    thermal_levels:  float = 0.0  # posterise bands (0 = smooth, >=2 = banded)

    # Cellular automata — Life / decay-diffusion overlay (feedback)
    ca_cell:     float = 8.0   # cell size in px (2..32)
    ca_rate:     float = 0.5   # evolution probability per frame (0..1)
    ca_seed_thr: float = 0.6   # luma threshold that seeds live cells (0..1)
    ca_mode:     float = 0.0   # 0 = Life (B3/S23), 1 = decay-diffusion
    ca_tint_r:   float = 0.31  # living-cell colour
    ca_tint_g:   float = 1.0
    ca_tint_b:   float = 0.55

    # Elastic grid — lattice mesh warp
    eg_grid:     float = 8.0   # nodes across (2..32)
    eg_strength: float = 0.5   # displacement amount (0..1)
    eg_speed:    float = 0.5   # animation speed (0..2)
    eg_jitter:   float = 0.5   # per-cell shear / chaos (0..1)
    eg_noise_mode: float = 0.0 # 0 = hash sin/cos, 1 = simplex flow (LYGIA)

    # CRT monitor — barrel + aperture grille + bloom (the project's only CRT;
    #  replaced the original scanline `crt` effect)
    crtm_curvature:       float = 0.30
    crtm_scanline:        float = 0.50
    crtm_grille:          float = 0.40
    crtm_vignette:        float = 0.40
    crtm_bloom:           float = 0.30
    crtm_aberration:      float = 0.50
    crtm_aberration_mode: float = 0.0  # 0 radial · 1 barrel+split · 2 scanline-phase

    # Glitch — block-displacement digital glitch (feedback)
    glitch_block_size:    float = 0.30   # band/macroblock size (0..1)
    glitch_drop_chance:   float = 0.30   # how often blocks glitch (0..1)
    glitch_repeat_chance: float = 0.20   # stuck/frozen blocks from prev frame (0..1)

    # Modulate — signal modulation (AM / FM / phase)
    mod_mode:   float = 0.0    # 0=AM 1=FM 2=phase
    mod_freq:   float = 20.0   # carrier frequency (cycles across frame)
    mod_amount: float = 0.5    # modulation depth (0..1)
    mod_speed:  float = 0.5    # carrier scroll speed
    mod_axis:   float = 0.0    # 0=vertical bars 1=horizontal (between=diagonal)

    # Pixelate — blocky pixelation with a recursive feedback loop
    pixelate_block:    float = 12.0   # block size in px (bigger = chunkier)
    pixelate_feedback: float = 0.90   # 0..1 history persistence / trail length
    pixelate_zoom:     float = 0.55   # 0..1 → feedback centre-zoom (0.5=neutral)
    pixelate_drift:    float = 0.30   # 0..1 → feedback vertical crawl
    pixelate_fb_mode:  float = 0.0    # 0=lighten(max) 1=add 2=over(mix)

    # ---- new effects (30–31.05 batch) ----
    # found_footage
    ff_dropout_density:  float = 0.40
    ff_dropout_strength: float = 0.70
    ff_warp_amount:      float = 0.008
    ff_ghost_opacity:    float = 0.12
    ff_chroma_bleed:     float = 2.50
    ff_luma_crush:       float = 0.40
    # chromatic_aberration
    ca_offset: float = 0.012
    ca_radial: float = 0.80
    ca_angle:  float = 0.00
    ca_spin:   float = 0.00
    # color_posterize
    post_levels:     float = 6.0
    post_color_mode: float = 0.0
    post_dither_mix: float = 0.30
    # swirl
    swirl_strength: float = 3.0
    swirl_radius:   float = 0.50
    swirl_cx:       float = 0.50
    swirl_cy:       float = 0.50
    swirl_animate:  float = 0.00
    # iridescent_foil
    ir_hue_range: float = 0.50
    ir_speed:     float = 0.40
    ir_metallic:  float = 0.60
    ir_scale:     float = 1.50
    ir_opacity:   float = 0.70
    # motion_blur
    mb_samples: float = 8.0
    mb_length:  float = 0.012
    mb_angle:   float = 0.00
    mb_radial:  float = 0.00
    mb_cx:      float = 0.50
    mb_cy:      float = 0.50
    # advanced_glitch
    ag_block_size:   float = 0.30
    ag_shift_amount: float = 0.04
    ag_chance:       float = 0.40
    ag_color_shift:  float = 0.40
    ag_speed:        float = 2.00

    # Wave warp — 3 wave layers
    wave_amp1: float = 0.30
    wave_amp2: float = 0.20
    wave_amp3: float = 0.10
    wave_freq1: float = 18.0
    wave_freq2: float = 47.0
    wave_freq3: float = 91.0
    wave_phase1: float = 0.00
    wave_phase2: float = 1.20
    wave_phase3: float = 2.40
    wave_speed1: float = 1.00
    wave_speed2: float = 1.70
    wave_speed3: float = 0.50
    wave_shape1: float = 0.0              # 0=sin 1=tri 2=saw 3=square 4=noise
    wave_shape2: float = 0.0
    wave_shape3: float = 4.0
    wave_angle: float = 0.50              # 0..1 → 0..2π displacement direction
    wave_polar: float = 0.00              # 0=cartesian, 1=polar drive
    wave_chromatic: float = 0.50
    wave_band_height: float = 1.00
    wave_band_y: float = 0.50
    wave_band_soft: float = 0.40

    # ASCII mosaic
    ascii_cell_size: float = 0.30        # 0..1 → 4..32 px
    ascii_invert: float = 0.00
    ascii_color_source: float = 0.00     # 0=mono, 1=colored from frame
    ascii_tint_r: float = 0.85
    ascii_tint_g: float = 1.00
    ascii_tint_b: float = 0.65
    # User-editable glyph string. Order matters: empty/light → dense/dark.
    # Default is " .:+=#@█". Renderer regenerates the atlas on change.
    ascii_text: str = " .:+=#@█"

    # User-editable HUD overlay text templates with placeholders:
    #   %TC%      — timecode MM:SS:FF
    #   %FRAME%   — frame counter (zero-padded)
    #   %WIDTH%, %HEIGHT% — current frame resolution
    #   %FACES%   — number of detected faces
    #   %INDEX%   — (per-face label only) face index 01..NN
    #   %EMOTION% — (per-face label) emotion name if available
    #   %CONF%    — (per-face label) detection confidence
    hud_text_top_left: str     = "%TC%   FACES %FACES%"
    hud_text_top_right: str    = "TRACKING"
    hud_text_bottom_left: str  = "FRM %FRAME%"
    hud_text_bottom_right: str = "RES %WIDTH%x%HEIGHT%"
    hud_text_face_label: str   = "FACE %INDEX%"
    # Blob label placeholders: %INDEX% %AREA% %X% %Y% %W% %H%
    hud_text_blob_label: str   = "BLOB %INDEX% A%AREA%"

    # Depth-mask band (used by depth_apply)
    depth_min: float = 0.00              # 0..1 (0=near, 1=far)
    depth_max: float = 0.50
    depth_feather: float = 0.10
    depth_invert: float = 0.00           # 0=keep effects in [min..max], 1=outside

    # Depth visualization (depth_view)
    depth_view_mode: float = 1.00        # 0=gray 1=heatmap 2=overlay
    depth_view_gain: float = 1.00
    depth_view_offset: float = 0.00
    depth_view_invert: float = 0.00

    # Stickers
    sticker_count: int = 30
    sticker_refresh: float = 2.5
    sticker_scale: float = 0.06
    sticker_chaos: float = 0.80
    sticker_brightness: float = 1.30

    # Runtime
    running: bool = False
    recording: bool = False

    # Pipeline
    effects: List[EffectConfig] = field(default_factory=default_pipeline)

    def to_dict(self) -> dict:
        return asdict(self)

    def load_dict(self, data: dict) -> None:
        valid = {f.name for f in fields(self)}
        for k, v in data.items():
            if k not in valid:
                continue
            if k == "effects" and isinstance(v, list):
                loaded = [
                    EffectConfig(
                        kind=e.get("kind", ""),
                        enabled=bool(e.get("enabled", True)),
                        mix=float(e.get("mix", 1.0)),
                    )
                    for e in v if e.get("kind") in EFFECT_KINDS
                ]
                # Merge: any kind that exists in default_pipeline but is
                # missing from the loaded preset gets appended as disabled
                # so new effects show up after an upgrade.
                have = {e.kind for e in loaded}
                for defcfg in default_pipeline():
                    if defcfg.kind not in have:
                        loaded.append(EffectConfig(
                            kind=defcfg.kind, enabled=False, mix=defcfg.mix))
                self.effects = loaded
            else:
                setattr(self, k, v)


class SharedState:
    """Params + Lock + latest frame + optical flow."""

    def __init__(self) -> None:
        self.params = Params()
        self.params_lock = threading.Lock()

        # Base values written by the GUI sliders; modulation engine reads these,
        # combines with sources, and writes the result to self.params.
        self.base_params: dict[str, float] = {}
        for f in fields(self.params):
            v = getattr(self.params, f.name)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.base_params[f.name] = float(v)
        # virtual targets for effect mix sliders: tag = "effect_mix_{kind}"
        for eff in self.params.effects:
            self.base_params[f"effect_mix_{eff.kind}"] = float(eff.mix)
        self.base_lock = threading.Lock()

        # Modulation routes (list of ModRoute-like dicts)
        self.mod_routes: list = []
        self.mod_routes_lock = threading.Lock()

        # Audio analysis bands (0..1 each)
        self.audio_bands: dict[str, float] = {
            "bass": 0.0, "mid": 0.0, "hi": 0.0, "peak": 0.0, "beat": 0.0,
        }
        self.audio_lock = threading.Lock()

        # Tracking-derived modulation values (0..1 each), computed by
        # DetectorThread from face/pose/hand/blob detections + optical flow.
        self.tracking: dict[str, float] = {}
        self.tracking_lock = threading.Lock()

        self.frame = None
        self.frame_lock = threading.Lock()
        self.frame_event = threading.Event()

        self.flow = None          # np.ndarray (h, w, 2) float32 normalized to UV
        self.flow_version = 0
        self.flow_lock = threading.Lock()

        # detection: latest face/pose result, written by capture, read by render
        self.detection = None
        self.detection_version = 0
        self.detection_lock = threading.Lock()

        # depth map (np.ndarray float32 (H, W), 0=near 1=far), set by DepthThread
        self.depth_map = None
        self.depth_version = 0
        self.depth_lock = threading.Lock()

        self.stop_event = threading.Event()

    def snapshot_params(self) -> Params:
        """Cheap thread-safe copy. Was using asdict + load_dict which iterated
        every field; copy.copy is O(1) for the dataclass and we only need to
        copy the `effects` list contents to avoid aliasing."""
        with self.params_lock:
            p = copy.copy(self.params)
            p.effects = [copy.copy(e) for e in self.params.effects]
            return p

    def update_param(self, name: str, value) -> None:
        """GUI/users write here. For numeric params we route through base_params
        so the modulation engine can add a modulated delta on top. Non-numeric
        (bool, str, list) go straight to params."""
        with self.params_lock:
            setattr(self.params, name, value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            with self.base_lock:
                self.base_params[name] = float(value)

    def snapshot_base_params(self) -> dict[str, float]:
        with self.base_lock:
            return dict(self.base_params)

    def apply_modulated_params(self, updates: dict[str, float]) -> None:
        """Called by ModulationEngine to overwrite effective values.

        Supports two kinds of target names:
          - regular Params attribute name → setattr on self.params
          - "effect_mix_{kind}" → writes into self.params.effects[i].mix
            (first occurrence of that kind in the pipeline)
        """
        with self.params_lock:
            for k, v in updates.items():
                if k.startswith("effect_mix_"):
                    kind = k[len("effect_mix_"):]
                    for eff in self.params.effects:
                        if eff.kind == kind:
                            eff.mix = float(v)
                            break
                elif hasattr(self.params, k):
                    setattr(self.params, k, v)

    # ---- modulation routes ----

    def add_mod_route(self, route) -> None:
        with self.mod_routes_lock:
            # replace existing route on the same target, if any
            self.mod_routes = [r for r in self.mod_routes if r.target != route.target]
            self.mod_routes.append(route)

    def remove_mod_route(self, target: str) -> None:
        with self.mod_routes_lock:
            self.mod_routes = [r for r in self.mod_routes if r.target != target]

    def get_mod_route(self, target: str):
        with self.mod_routes_lock:
            for r in self.mod_routes:
                if r.target == target:
                    return r
            return None

    def snapshot_mod_routes(self) -> list:
        with self.mod_routes_lock:
            return list(self.mod_routes)

    # ---- audio ----

    def set_audio_bands(self, bands: dict) -> None:
        with self.audio_lock:
            self.audio_bands = dict(bands)

    def get_audio_bands(self) -> dict:
        with self.audio_lock:
            return dict(self.audio_bands)

    def set_tracking(self, values: dict) -> None:
        with self.tracking_lock:
            self.tracking = dict(values)

    def get_tracking(self) -> dict:
        with self.tracking_lock:
            return dict(self.tracking)

    def update_effect(self, idx: int, *, enabled: bool | None = None,
                      mix: float | None = None,
                      blend: float | None = None) -> None:
        with self.params_lock:
            if 0 <= idx < len(self.params.effects):
                eff = self.params.effects[idx]
                if enabled is not None:
                    eff.enabled = enabled
                if blend is not None:
                    eff.blend = float(blend)
                if mix is not None:
                    eff.mix = float(mix)
                    # also write to base_params so the modulation engine
                    # treats this as the new dry-mix baseline
                    with self.base_lock:
                        self.base_params[f"effect_mix_{eff.kind}"] = float(mix)

    def move_effect(self, idx: int, delta: int) -> None:
        with self.params_lock:
            eff = self.params.effects
            j = idx + delta
            if 0 <= idx < len(eff) and 0 <= j < len(eff):
                eff[idx], eff[j] = eff[j], eff[idx]

    def set_frame(self, frame) -> None:
        with self.frame_lock:
            self.frame = frame
        self.frame_event.set()

    def get_frame(self):
        with self.frame_lock:
            return self.frame

    def set_flow(self, flow) -> None:
        with self.flow_lock:
            self.flow = flow
            self.flow_version += 1

    def get_flow(self) -> tuple[object, int]:
        with self.flow_lock:
            return self.flow, self.flow_version

    def set_detection(self, det) -> None:
        with self.detection_lock:
            self.detection = det
            self.detection_version += 1

    def get_detection(self) -> tuple[object, int]:
        with self.detection_lock:
            return self.detection, self.detection_version

    def set_depth_map(self, dm) -> None:
        with self.depth_lock:
            self.depth_map = dm
            self.depth_version += 1

    def get_depth_map(self):
        with self.depth_lock:
            return self.depth_map, self.depth_version

    def save_preset(self, path: Path) -> None:
        with self.params_lock:
            data = self.params.to_dict()
        data.pop("running", None)
        data.pop("recording", None)
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_preset(self, path: Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        with self.params_lock:
            self.params.load_dict(data)
        # re-seed base_params from the loaded values so modulation doesn't keep
        # modulating against stale bases.
        with self.base_lock:
            for f in fields(self.params):
                v = getattr(self.params, f.name)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    self.base_params[f.name] = float(v)
            for eff in self.params.effects:
                self.base_params[f"effect_mix_{eff.kind}"] = float(eff.mix)
