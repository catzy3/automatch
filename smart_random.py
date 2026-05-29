"""Smart randomization.

Two parts:

1. SMART_RANGES — per-parameter "pretty" ranges, tighter than the full slider
   range. Random values in these never produce ugly extremes (full saturation,
   total mute, infinite trails, etc.).

2. ENABLE_WEIGHTS — per-effect probability of being on in a Random pipeline.
   "Base" effects (luma_key, dual_tone, heavy_grain) are almost always on so
   the image keeps a coherent look; "accent" effects are rare; "edge-case"
   effects are very rare.

3. MIX_RANGE — Random mix is 0.35..0.95 (no fully-dry pixels, no extreme wet).
"""
from __future__ import annotations

import random
from typing import Dict, Tuple


# (vmin_pretty, vmax_pretty) per parameter tag
SMART_RANGES: Dict[str, Tuple[float, float]] = {
    # global tone
    "gain": (0.9, 1.4),
    "contrast": (0.8, 1.5),
    "saturation": (0.6, 1.4),

    # luma_key
    "luma_lift":   (-0.10, 0.10),
    "luma_gain":   (1.00, 1.80),
    "luma_gamma":  (0.60, 1.40),
    "luma_invert": (0.00, 0.00),

    # duotone
    "duotone_midpoint": (0.40, 0.70),
    "duotone_strength": (0.70, 1.00),
    "accent_r": (0.50, 1.00),
    "accent_g": (0.20, 0.65),
    "accent_b": (0.00, 0.40),

    # dual_tone (3-stop)
    "dual_shadow_r": (0.00, 0.20),
    "dual_shadow_g": (0.10, 0.55),
    "dual_shadow_b": (0.40, 0.75),
    "dual_mid_r":    (0.55, 1.00),
    "dual_mid_g":    (0.20, 0.65),
    "dual_mid_b":    (0.00, 0.30),
    "dual_high_r":   (0.85, 1.00),
    "dual_high_g":   (0.70, 1.00),
    "dual_high_b":   (0.30, 0.65),
    "dual_split":    (0.50, 0.75),
    "dual_strength": (0.70, 1.00),

    # motion_trails — interesting but not destructive
    "trails_decay":     (0.85, 0.97),
    "trails_threshold": (0.10, 0.40),
    "trails_blur":      (0.20, 0.70),
    "trails_drift_x":   (-0.30, 0.30),
    "trails_drift_y":   (-0.50, 0.20),
    "trails_secondary": (0.10, 0.50),
    "trails_mode":      (0.00, 2.50),  # screen / max / add (skip overlay extreme)
    "trails_polarity":  (0.00, 0.10),  # mostly bright trails
    "trails_chaos":     (0.10, 0.40),
    "trails_tint_r":    (0.60, 1.20),
    "trails_tint_g":    (0.40, 0.90),
    "trails_tint_b":    (0.20, 0.70),

    # heavy_grain
    "grain_amount": (0.30, 0.85),
    "grain_size":   (0.10, 0.50),
    "grain_chroma": (0.05, 0.30),
    "grain_blotch": (0.10, 0.40),

    # bloom_glow
    "bloom_threshold": (0.40, 0.70),
    "bloom_radius":    (0.30, 0.70),
    "bloom_intensity": (0.60, 1.50),
    "bloom_tint_r":    (0.70, 1.00),
    "bloom_tint_g":    (0.60, 1.00),
    "bloom_tint_b":    (0.30, 0.80),

    # pixel_swap
    "pixel_swap_threshold":   (0.05, 0.30),
    "pixel_swap_radius":      (0.10, 0.50),
    "pixel_swap_chaos":       (0.30, 0.70),
    "pixel_swap_directional": (0.00, 0.40),
    "pixel_swap_temporal":    (0.20, 0.70),

    # mirror_flow
    "mirror_drip":   (0.005, 0.020),
    "mirror_decay":  (0.88, 0.97),
    "mirror_unroll": (0.50, 0.95),
    "mirror_axis_h": (0.20, 1.00),
    "mirror_axis_v": (0.00, 0.50),
    "mirror_swirl":  (0.00, 0.40),
    "mirror_chaos":  (0.20, 0.60),

    # datamosh
    "datamosh_strength":     (0.60, 1.10),
    "datamosh_freeze":       (0.20, 0.60),
    "datamosh_period":       (1.50, 6.00),
    "datamosh_breakthrough": (0.05, 0.20),
    "datamosh_jitter":       (0.10, 0.40),
    "datamosh_chaos":        (0.30, 0.60),

    # cctv_grid
    "cctv_cols":              (1.00, 3.00),
    "cctv_rows":              (1.00, 3.00),
    "cctv_gap":               (0.00, 0.015),
    "cctv_invert_mask":       (0.0, 15.0),
    "cctv_delay_mask":        (0.0, 65535.0),
    "cctv_tint_strength":     (0.30, 0.60),
    "cctv_desat":             (0.20, 0.50),
    "cctv_label_brightness":  (0.80, 1.50),

    # vhs
    "vhs_tracking_noise": (0.15, 0.50),
    "vhs_line_jitter":    (0.10, 0.40),
    "vhs_chroma_blur":    (0.30, 0.60),
    "vhs_color_loss":     (0.10, 0.40),
    "vhs_head_switch":    (0.20, 0.50),

    # fragment_layers
    "frag_count":  (0.30, 0.60),
    "frag_height": (0.20, 0.50),
    "frag_offset": (0.20, 0.50),
    "frag_alpha":  (0.40, 0.80),

    # pixel_sort
    "psort_threshold":    (0.35, 0.60),
    "psort_length":       (0.25, 0.65),
    "psort_length_var":   (0.10, 0.50),
    "psort_direction":    (-1.0, 1.0),
    "psort_band_density": (0.30, 0.80),
    "psort_band_jitter":  (0.10, 0.50),
    "psort_band_speed":   (0.20, 0.70),
    "psort_edge_fade":    (0.05, 0.40),
    "psort_color_mode":   (0.0,  3.99),
    "psort_axis":         (0.0,  1.0),    # both axes valid
    "psort_algorithm":    (0.0,  1.99),   # mostly brightest/darkest
    "psort_chromatic":    (0.15, 0.55),
    "psort_blend":        (0.0,  2.0),

    # ascii_mosaic
    "ascii_cell_size":    (0.10, 0.50),
    "ascii_invert":       (0.0, 0.0),
    "ascii_color_source": (0.0, 1.0),
    "ascii_tint_r":       (0.4, 1.0),
    "ascii_tint_g":       (0.6, 1.0),
    "ascii_tint_b":       (0.3, 0.9),

    # wave_warp
    "wave_amp1":   (0.10, 0.50), "wave_freq1":  (8.0, 35.0),
    "wave_phase1": (0.0, 6.28),  "wave_speed1": (0.5, 2.5), "wave_shape1": (0.0, 1.99),
    "wave_amp2":   (0.05, 0.30), "wave_freq2":  (20.0, 80.0),
    "wave_phase2": (0.0, 6.28),  "wave_speed2": (-2.0, 2.0), "wave_shape2": (0.0, 2.99),
    "wave_amp3":   (0.02, 0.20), "wave_freq3":  (40.0, 120.0),
    "wave_phase3": (0.0, 6.28),  "wave_speed3": (-1.0, 1.0), "wave_shape3": (0.0, 4.99),
    "wave_angle":      (0.0, 1.0),
    "wave_polar":      (0.0, 0.3),
    "wave_chromatic":  (0.30, 0.90),
    "wave_band_height":(0.50, 1.00),
    "wave_band_y":     (0.30, 0.70),
    "wave_band_soft":  (0.30, 0.60),

    # rgb_split — chaotic, narrow it
    "rgb_shift_amount":   (0.10, 0.40),
    "rgb_wave_frequency": (4.00, 16.0),
    "rgb_wave_speed":     (0.50, 2.50),

    # glitch (macroblock)
    "glitch_block_size":    (0.10, 0.40),
    "glitch_drop_chance":   (0.05, 0.25),
    "glitch_repeat_chance": (0.05, 0.20),

    # crt
    "crt_scanline_strength": (0.25, 0.60),
    "crt_mask_strength":     (0.15, 0.45),
    "crt_curvature":         (0.05, 0.25),
    "crt_vignette":          (0.20, 0.50),
    "crt_persistence":       (0.00, 0.30),

    # drip
    "drip_speed": (0.005, 0.020),
    "drip_decay": (0.90, 0.97),

    # dither (circuit modulation) — keep pretty
    "dither_levels":          (3.00, 8.00),
    "dither_grid_size":       (2.00, 8.00),
    "dither_osc_a_freq":      (6.00, 25.0),
    "dither_osc_b_freq":      (4.00, 22.0),
    "dither_osc_a_speed":     (0.10, 1.20),
    "dither_osc_b_speed":     (0.05, 0.90),
    "dither_mix_mode":        (0.00, 3.00),
    "dither_trace_thickness": (0.05, 0.25),
    "dither_jitter":          (0.00, 0.25),
    "dither_chromatic":       (0.00, 0.50),
    "dither_polarity":        (0.00, 1.00),
    "dither_color_mode":      (0.00, 2.00),

    # quad_split
    "quad_gap":         (0.0, 0.015),
    "quad_invert_mask": (0.0, 15.0),
    "quad_freeze":      (0.5, 1.0),

    # hud_overlay (line thickness as a slider only)
    "hud_brightness":     (0.8, 1.8),
    "hud_line_thickness": (1.0, 3.0),
}


# Probability that an effect is enabled when randomizing the pipeline.
# Base effects are almost always on; accents and edge cases are rare.
ENABLE_WEIGHTS: Dict[str, float] = {
    # base — almost always on
    "luma_key":    0.95,
    "dual_tone":   0.85,
    "heavy_grain": 0.80,
    "duotone":     0.30,  # mutually overlaps dual_tone, less often

    # character — half the time
    "motion_trails":   0.60,
    "bloom_glow":      0.55,

    # accents — sometimes
    "datamosh":        0.30,
    "fragment_layers": 0.25,
    "vhs":             0.20,
    "pixel_sort":      0.35,
    "wave_warp":       0.30,
    "ascii_mosaic":    0.15,

    # edge cases — rare
    "pixel_swap":  0.15,
    "mirror_flow": 0.15,
    "cctv_grid":   0.10,
    "rgb_split":   0.15,
    "glitch":      0.10,
    "crt":         0.10,
    "drip":        0.10,
    "dither":      0.10,
    "quad_split":  0.05,
    "hud_overlay": 0.10,
}


MIX_RANGE = (0.35, 0.95)


# Map effect kind → list of parameter tags that belong to it.
# Driven by tag prefixes so it stays in sync with SMART_RANGES.
KIND_TO_PARAMS: dict[str, list[str]] = {
    "luma_key":     ["luma_lift", "luma_gain", "luma_gamma", "luma_invert"],
    "duotone":      ["accent_r", "accent_g", "accent_b",
                     "duotone_midpoint", "duotone_strength"],
    "dual_tone":    ["dual_shadow_r", "dual_shadow_g", "dual_shadow_b",
                     "dual_mid_r", "dual_mid_g", "dual_mid_b",
                     "dual_high_r", "dual_high_g", "dual_high_b",
                     "dual_split", "dual_strength"],
    "motion_trails":["trails_decay", "trails_threshold", "trails_blur",
                     "trails_drift_x", "trails_drift_y",
                     "trails_tint_r", "trails_tint_g", "trails_tint_b",
                     "trails_secondary", "trails_mode", "trails_polarity",
                     "trails_chaos"],
    "heavy_grain":  ["grain_amount", "grain_size", "grain_chroma", "grain_blotch"],
    "bloom_glow":   ["bloom_threshold", "bloom_radius", "bloom_intensity",
                     "bloom_tint_r", "bloom_tint_g", "bloom_tint_b"],
    "fragment_layers": ["frag_count", "frag_height", "frag_offset", "frag_alpha"],
    "pixel_sort":   ["psort_threshold", "psort_length", "psort_length_var",
                     "psort_direction", "psort_band_density",
                     "psort_band_jitter", "psort_band_speed", "psort_edge_fade",
                     "psort_color_mode", "psort_axis", "psort_algorithm",
                     "psort_chromatic", "psort_blend"],
    "wave_warp":    ["wave_amp1", "wave_freq1", "wave_phase1", "wave_speed1", "wave_shape1",
                     "wave_amp2", "wave_freq2", "wave_phase2", "wave_speed2", "wave_shape2",
                     "wave_amp3", "wave_freq3", "wave_phase3", "wave_speed3", "wave_shape3",
                     "wave_angle", "wave_polar", "wave_chromatic",
                     "wave_band_height", "wave_band_y", "wave_band_soft"],
    "ascii_mosaic": ["ascii_cell_size", "ascii_invert", "ascii_color_source",
                     "ascii_tint_r", "ascii_tint_g", "ascii_tint_b"],
    "datamosh":     ["datamosh_strength", "datamosh_freeze", "datamosh_period",
                     "datamosh_breakthrough", "datamosh_jitter", "datamosh_chaos"],
    "pixel_swap":   ["pixel_swap_threshold", "pixel_swap_radius",
                     "pixel_swap_chaos", "pixel_swap_directional",
                     "pixel_swap_temporal"],
    "mirror_flow":  ["mirror_drip", "mirror_decay", "mirror_unroll",
                     "mirror_axis_h", "mirror_axis_v", "mirror_swirl",
                     "mirror_chaos"],
    "vhs":          ["vhs_tracking_noise", "vhs_line_jitter", "vhs_chroma_blur",
                     "vhs_color_loss", "vhs_head_switch"],
    "rgb_split":    ["rgb_shift_amount", "rgb_wave_frequency", "rgb_wave_speed"],
    "glitch":       ["glitch_block_size", "glitch_drop_chance", "glitch_repeat_chance"],
    "crt":          ["crt_scanline_strength", "crt_mask_strength",
                     "crt_curvature", "crt_vignette", "crt_persistence"],
    "drip":         ["drip_speed", "drip_decay"],
    "dither":       ["dither_levels", "dither_grid_size",
                     "dither_osc_a_freq", "dither_osc_b_freq",
                     "dither_osc_a_speed", "dither_osc_b_speed",
                     "dither_mix_mode", "dither_trace_thickness",
                     "dither_jitter", "dither_chromatic",
                     "dither_polarity", "dither_color_mode"],
    "cctv_grid":    ["cctv_cols", "cctv_rows", "cctv_gap",
                     "cctv_invert_mask", "cctv_delay_mask",
                     "cctv_tint_strength", "cctv_desat", "cctv_label_brightness"],
    "quad_split":   ["quad_gap", "quad_invert_mask", "quad_freeze"],
    "hud_overlay":  ["hud_brightness", "hud_line_thickness"],
    "depth_apply":  ["depth_min", "depth_max", "depth_feather", "depth_invert"],
    "depth_view":   ["depth_view_mode", "depth_view_gain",
                     "depth_view_offset", "depth_view_invert"],
}


def randomize_effect(state, kind: str, set_value_cb=None) -> None:
    """Roll new pretty-range values for every parameter that belongs to one
    effect. Triggered by the 🎲 button on a pipeline row."""
    rng = random.Random()
    tags = KIND_TO_PARAMS.get(kind, [])
    for tag in tags:
        lo, hi = SMART_RANGES.get(tag, (0.0, 1.0))
        v = rng.uniform(lo, hi)
        if hasattr(state.params, tag):
            state.update_param(tag, v)
            if set_value_cb is not None:
                try:
                    set_value_cb(tag, v)
                except Exception:
                    pass


# ----- audio-route randomization -----

# Parameters that respond well to audio reactivity (selected by hand).
INTERESTING_AUDIO_TARGETS: list[str] = [
    "bloom_intensity", "bloom_radius",
    "trails_decay", "trails_blur", "trails_secondary",
    "grain_amount", "grain_blotch",
    "vhs_tracking_noise", "vhs_line_jitter",
    "datamosh_strength", "datamosh_freeze",
    "wave_amp1", "wave_speed", "wave_chromatic",
    "psort_length", "psort_chromatic", "psort_band_density",
    "luma_gain",
    "mirror_unroll", "mirror_swirl",
    "frag_alpha",
    "ascii_cell_size",
    # mix-targets — modulate the dry/wet of entire effects
    "effect_mix_datamosh", "effect_mix_pixel_sort", "effect_mix_wave_warp",
    "effect_mix_bloom_glow", "effect_mix_motion_trails",
    "effect_mix_fragment_layers",
]


def randomize_audio_routes(state, param_ranges: dict[str, tuple],
                           max_routes: int = 4) -> None:
    """Replace existing routes with up to `max_routes` random audio links.

    Picks targets weighted by interest, draws sensible source/gain/threshold/
    curve/range_min/range_max/invert/rise/fall for each.
    """
    from modulation import ModRoute

    rng = random.Random()

    # clear all existing routes first
    for r in list(state.snapshot_mod_routes()):
        state.remove_mod_route(r.target)

    n = rng.randint(1, max(1, int(max_routes)))
    candidates = list(INTERESTING_AUDIO_TARGETS)
    rng.shuffle(candidates)
    targets = candidates[:n]

    sources = ["bass", "mid", "hi", "peak"]
    src_weights = [3, 2, 1, 3]   # bass + peak punch more, hi is fragile

    base = state.snapshot_base_params()

    for target in targets:
        if target not in param_ranges:
            continue
        vmin, vmax = param_ranges[target]
        cur = base.get(target, (vmin + vmax) * 0.5)
        # choose a window around current base, biased toward bigger excursions
        span = (vmax - vmin)
        center = cur
        lo = max(vmin, center - span * rng.uniform(0.05, 0.45))
        hi = min(vmax, center + span * rng.uniform(0.20, 0.70))
        if hi - lo < 1e-3:
            lo, hi = vmin, vmax

        route = ModRoute(
            target=target,
            source=rng.choices(sources, weights=src_weights)[0],
            gain=rng.uniform(1.0, 2.5),
            threshold=rng.uniform(0.04, 0.18),
            curve=rng.uniform(0.6, 2.0),
            range_min=lo,
            range_max=hi,
            invert=rng.random() < 0.12,
            smooth_rise=rng.uniform(0.55, 0.95),
            smooth_fall=rng.uniform(0.05, 0.25),
        )
        state.add_mod_route(route)


def randomize_params(state, set_value_cb=None, rng=None) -> None:
    """Pick a value in each parameter's pretty range and write it to base/params.
    set_value_cb(tag, value) is called for each updated tag (for GUI sync)."""
    if rng is None:
        rng = random.Random()
    for tag, (lo, hi) in SMART_RANGES.items():
        v = rng.uniform(lo, hi)
        if hasattr(state.params, tag):
            state.update_param(tag, v)
            if set_value_cb is not None:
                try:
                    set_value_cb(tag, v)
                except Exception:
                    pass


def randomize_pipeline(state, rng=None) -> None:
    """Toggle enabled state per ENABLE_WEIGHTS and assign a pretty mix."""
    if rng is None:
        rng = random.Random()
    params = state.snapshot_params()
    for idx, eff in enumerate(params.effects):
        weight = ENABLE_WEIGHTS.get(eff.kind, 0.20)
        enabled = rng.random() < weight
        mix = rng.uniform(*MIX_RANGE) if enabled else 0.5
        state.update_effect(idx, enabled=enabled, mix=mix)


def smart_random(state, set_value_cb=None, seed=None) -> None:
    """One-call entry point: randomize both params and pipeline.
    Pass `seed` for deterministic / reproducible output."""
    rng = random.Random(seed) if seed is not None else random.Random()
    randomize_params(state, set_value_cb=set_value_cb, rng=rng)
    randomize_pipeline(state, rng=rng)
