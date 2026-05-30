#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  color_posterize.frag  —  colour quantisation / palette-snap posteriser
//
//  Three selectable modes:
//    0  luma-posterize  — convert to luma, quantise, reconstruct hue
//    1  per-channel     — quantise R, G, B independently
//    2  palette-snap    — hard-snap each pixel to the nearest of 8 neon colours
//
//  An optional Bayer 4×4 dither blends (u_dither_mix) between the raw
//  quantised value and the next level up, softening the sharp step edges
//  without blurring the image spatially.
//
//  Pipeline:
//    1. sample u_tex
//    2. quantise with floor(col*(L-1)+0.5)/(L-1)   [luma or per-channel]
//       OR snap to neon palette (mode 2)
//    3. Bayer 4×4 dither blend at level boundaries  [u_dither_mix > 0]
//    4. mix(dry, wet, u_mix)
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;           // 0..1   — dry/wet blend

uniform float u_levels;        // 2..32  — quantisation steps per channel
uniform float u_color_mode;    // 0=luma 1=per-channel 2=palette-snap
uniform float u_dither_mix;    // 0..1   — Bayer dither strength at step edges

in  vec2 v_uv;
out vec4 out_color;

// ─────────────────────────────────────────────────────────────────── Bayer 4×4

// Standard 4×4 Bayer ordered-dither matrix, values 0..15 → normalised 0..1.
// Unrolled to avoid driver-specific const-array indexing bugs.
float bayer4(ivec2 p) {
    int x = p.x & 3;
    int y = p.y & 3;
    int idx = (y << 2) | x;
    if (idx ==  0) return  0.0 / 16.0;
    if (idx ==  1) return  8.0 / 16.0;
    if (idx ==  2) return  2.0 / 16.0;
    if (idx ==  3) return 10.0 / 16.0;
    if (idx ==  4) return 12.0 / 16.0;
    if (idx ==  5) return  4.0 / 16.0;
    if (idx ==  6) return 14.0 / 16.0;
    if (idx ==  7) return  6.0 / 16.0;
    if (idx ==  8) return  3.0 / 16.0;
    if (idx ==  9) return 11.0 / 16.0;
    if (idx == 10) return  1.0 / 16.0;
    if (idx == 11) return  9.0 / 16.0;
    if (idx == 12) return 15.0 / 16.0;
    if (idx == 13) return  7.0 / 16.0;
    if (idx == 14) return 13.0 / 16.0;
    return                 5.0 / 16.0;
}

// ─────────────────────────────────────────────── quantise helpers

// Quantise a single value v ∈ [0,1] to (levels-1) evenly-spaced steps.
// Uses round-to-nearest: floor(v*(L-1)+0.5)/(L-1).
float quantise(float v, float Lm1) {
    return floor(v * Lm1 + 0.5) / Lm1;
}

// Dither-blend between the quantised value and the next step up.
// threshold is the Bayer matrix value at this pixel (0..1).
// residual is the fractional distance from the current step to the next.
// mix_strength controls how strongly the dither is applied.
float dither_step(float quantised, float Lm1, float residual,
                  float threshold, float mix_strength) {
    // promote to next level where residual > dithered threshold
    float promote = step(mix(0.5, threshold, mix_strength), residual);
    return clamp(quantised + promote / Lm1, 0.0, 1.0);
}

// ─────────────────────────────────────────────── neon palette (mode 2)

// 8-colour neon palette used for palette-snap mode.
// Ordered: black, cyan, magenta, green, red, yellow, white, blue.
vec3 palette_color(int i) {
    if (i == 0) return vec3(0.0,  0.0,  0.0 );  // black
    if (i == 1) return vec3(0.0,  1.0,  1.0 );  // cyan
    if (i == 2) return vec3(1.0,  0.0,  1.0 );  // magenta
    if (i == 3) return vec3(0.0,  1.0,  0.0 );  // green
    if (i == 4) return vec3(1.0,  0.0,  0.0 );  // red
    if (i == 5) return vec3(1.0,  1.0,  0.0 );  // yellow
    if (i == 6) return vec3(1.0,  1.0,  1.0 );  // white
    return              vec3(0.0,  0.0,  1.0 );  // blue  (i == 7)
}

// Snap col to the nearest palette entry by squared RGB distance.
// Returns the snapped colour.  Secondary return (out_resid) is the
// normalised distance to the nearest entry, used for dither blending.
vec3 palette_snap(vec3 col, out float out_resid) {
    float best_dist = 1e9;
    int   best_idx  = 0;
    float second_dist = 1e9;

    for (int i = 0; i < 8; ++i) {
        vec3  diff = col - palette_color(i);
        float d    = dot(diff, diff);   // squared Euclidean in RGB
        if (d < best_dist) {
            second_dist = best_dist;
            best_dist   = d;
            best_idx    = i;
        } else if (d < second_dist) {
            second_dist = d;
        }
    }

    // residual: how far col is between the nearest and second-nearest entry
    // mapped to 0..1 so Bayer dither can decide whether to snap to 2nd-nearest
    float gap = max(second_dist - best_dist, 1e-7);
    out_resid = clamp(best_dist / gap, 0.0, 1.0);

    return palette_color(best_idx);
}

// ─────────────────────────────────────────────────────────────────────── main

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // clamp levels to valid range
    float L   = clamp(floor(u_levels + 0.5), 2.0, 32.0);
    float Lm1 = L - 1.0;

    // Bayer threshold at this pixel (per-pixel, 0..1)
    ivec2 pix_i    = ivec2(v_uv * u_resolution);
    float bayer_th = bayer4(pix_i);
    float dm       = clamp(u_dither_mix, 0.0, 1.0);

    vec3 wet;
    int  mode = int(clamp(u_color_mode, 0.0, 2.0));

    if (mode == 0) {
        // ── luma-posterize ─────────────────────────────────────────────────
        // Quantise luma only; scale chroma down proportionally to preserve hue.
        float luma    = dot(dry, vec3(0.299, 0.587, 0.114));
        float luma_q  = quantise(luma, Lm1);

        // residual for dither: fractional position within the current step
        float residual = fract(luma * Lm1);
        float luma_d   = dither_step(luma_q, Lm1, residual, bayer_th, dm);

        // avoid divide-by-zero when luma ≈ 0
        float scale = (luma > 1e-4) ? (luma_d / luma) : 0.0;
        wet = clamp(dry * scale, 0.0, 1.0);

    } else if (mode == 1) {
        // ── per-channel ────────────────────────────────────────────────────
        vec3 q;
        q.r = quantise(dry.r, Lm1);
        q.g = quantise(dry.g, Lm1);
        q.b = quantise(dry.b, Lm1);

        // per-channel Bayer dither with small per-channel Bayer phase offsets
        // to reduce cross-channel alignment artefacts
        vec3 resid = fract(dry * Lm1);
        wet.r = dither_step(q.r, Lm1, resid.r,
                    bayer4(pix_i + ivec2(0, 0)), dm);
        wet.g = dither_step(q.g, Lm1, resid.g,
                    bayer4(pix_i + ivec2(1, 2)), dm);
        wet.b = dither_step(q.b, Lm1, resid.b,
                    bayer4(pix_i + ivec2(3, 1)), dm);

    } else {
        // ── palette-snap (mode 2) ──────────────────────────────────────────
        float resid;
        wet = palette_snap(dry, resid);

        // When dither_mix > 0, allow the Bayer matrix to keep the original
        // colour where the snap error is small (smooth transitions at palette
        // boundaries).  Uses the residual as a soft gate.
        if (dm > 0.001) {
            float blend = smoothstep(0.0, 1.0,
                              mix(0.5, bayer_th, dm) - resid * 0.5);
            wet = mix(wet, dry, blend * dm * 0.4);
            wet = clamp(wet, 0.0, 1.0);
        }
    }

    out_color = vec4(mix(dry, wet, u_mix), 1.0);
}
