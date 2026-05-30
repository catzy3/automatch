#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  dither.frag  —  "circuit modulation" dither
//
//  Posterises the input to N levels, then perturbs the per-pixel quantisation
//  threshold with a structured pattern produced by TWO cross-modulated
//  oscillators on a coarse grid.  The result reads like an oscilloscope
//  trace or a partially-soldered circuit board — much more deliberate than
//  Bayer / blue-noise / Floyd-Steinberg dither.
//
//  Pipeline:
//    1.  quantise rgb to (levels-1) steps           ← stepped
//    2.  build a 2-D oscillator field on a grid     ← osc_a, osc_b
//    3.  combine the oscillators via u_mix_mode     ← pattern in 0..1
//    4.  add per-cell random jitter                 ← pattern
//    5.  optionally invert (polarity)
//    6.  threshold to a "trace" mask (smoothstep)   ← trace in 0..1
//    7.  step pixel up to the next level where the
//        pattern threshold is below the residual    ← dith
//    8.  remap colour through u_color_mode:
//          0 = mono     — luma drives mix(a, b)
//          1 = channels — keep posterised rgb
//          2 = duotone  — hard binary, no greys
//
//  NOTE: u_mix is NOT consulted — dither is "without wet/dry" by design.
//        The GUI hides the slider via EFFECTS_NO_MIX (gui.py).
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform sampler2D u_blue_noise;     // optional blue-noise tile (algo==3)
uniform vec2  u_resolution;
uniform float u_time;

uniform float u_levels;             // 2..32   — quantisation levels
uniform float u_grid_size;          // 1..32   — grid cell pixels
uniform float u_osc_a_freq;         // 0.5..50 — oscillator A spatial freq
uniform float u_osc_b_freq;         // 0.5..50 — oscillator B spatial freq
uniform float u_osc_a_speed;        // 0..3    — oscillator A temporal speed
uniform float u_osc_b_speed;        // 0..3    — oscillator B temporal speed
uniform float u_mix_mode;           // 0=avg 1=mul 2=xor 3=cross-mod
uniform float u_trace_thickness;    // 0..0.5  — softness of trace mask
uniform float u_jitter;             // 0..1    — random noise added to pattern
uniform float u_chromatic;          // 0..1    — phase offset per channel
uniform float u_polarity;           // 0|1     — invert pattern
uniform float u_color_mode;         // 0=mono 1=channels 2=duotone
uniform vec3  u_color_a;            // shadow / off colour
uniform vec3  u_color_b;            // highlight / on colour

// 0 = circuit modulation (current default)
// 1 = Bayer 4×4 ordered dither
// 2 = Bayer 8×8 ordered dither
// 3 = Blue noise dither (uses u_blue_noise texture)
uniform float u_algorithm;

in  vec2 v_uv;
out vec4 out_color;

// ───────────────────────────────────────────── helpers

float hash21(vec2 p) {
    p = fract(p * vec2(123.45, 678.91));
    p += dot(p, p.yx + 45.32);
    return fract(p.x * p.y);
}

// One non-trivial oscillator: a sine in x whose phase is wobbled by another
// sine in y.  Output is normalised to 0..1.
float oscillator(vec2 p, float freq, float speed, float t) {
    return sin( p.x * freq
              + sin(p.y * freq * 1.37 + t * speed * 0.7)
              + t * speed ) * 0.5 + 0.5;
}

// Pattern for one channel.  Phase offset shifts the whole field, used by
// u_chromatic so r/g/b end up on different traces.
float circuit_pattern(vec2 grid_uv, float t, float chan_phase) {
    float a = oscillator(grid_uv + vec2(chan_phase * 0.13, 0.0),
                         u_osc_a_freq, u_osc_a_speed, t);
    float b = oscillator(grid_uv.yx + vec2(chan_phase * 0.21, 0.0),
                         u_osc_b_freq, u_osc_b_speed, t + 1.7);

    float p;
    if      (u_mix_mode < 0.5) p = (a + b) * 0.5;                         // avg
    else if (u_mix_mode < 1.5) p = clamp(a * b * 1.6, 0.0, 1.0);          // mul
    else if (u_mix_mode < 2.5) p = clamp(abs(a - b) * 1.6, 0.0, 1.0);     // xor-ish
    else {                                                                // cross-mod
        p = sin(a * 6.2831 + b * 4.0 + t * 0.4) * 0.5 + 0.5;
    }
    return p;
}

// ───────────────────────────────────────────── Bayer matrices

// Standard 4×4 Bayer matrix, values 0..15 normalised by /16 to 0..1.
float bayer4(ivec2 p) {
    int x = p.x & 3;
    int y = p.y & 3;
    int idx = (y << 2) | x;
    // unrolled lookup — GLSL can't index const arrays reliably across drivers
    if (idx ==  0) return  0.0/16.0;
    if (idx ==  1) return  8.0/16.0;
    if (idx ==  2) return  2.0/16.0;
    if (idx ==  3) return 10.0/16.0;
    if (idx ==  4) return 12.0/16.0;
    if (idx ==  5) return  4.0/16.0;
    if (idx ==  6) return 14.0/16.0;
    if (idx ==  7) return  6.0/16.0;
    if (idx ==  8) return  3.0/16.0;
    if (idx ==  9) return 11.0/16.0;
    if (idx == 10) return  1.0/16.0;
    if (idx == 11) return  9.0/16.0;
    if (idx == 12) return 15.0/16.0;
    if (idx == 13) return  7.0/16.0;
    if (idx == 14) return 13.0/16.0;
    return                5.0/16.0;
}

// 8×8 generated as 4×bayer4 + bayer2 sublattice (standard recursion).
float bayer8(ivec2 p) {
    ivec2 q = p & 7;
    ivec2 hi = q >> 1;          // 0..3
    ivec2 lo = q & 1;           // 0..1
    int bayer2 = (lo.y << 1) | lo.x;
    // bayer2 → {0,2,3,1} / 4
    float b2;
    if      (bayer2 == 0) b2 = 0.0/4.0;   // M2[0][0] = 0
    else if (bayer2 == 1) b2 = 2.0/4.0;   // M2[0][1] = 2
    else if (bayer2 == 2) b2 = 3.0/4.0;   // M2[1][0] = 3
    else                  b2 = 1.0/4.0;   // M2[1][1] = 1
    return (bayer4(hi) + b2 / 16.0) * (16.0 / 17.0) + 0.5/64.0;
}

// ───────────────────────────────────────────── main

void main() {
    vec3 col = texture(u_tex, v_uv).rgb;

    // 1 — quantise
    float L      = max(2.0, u_levels);
    float Lm1    = L - 1.0;
    vec3 scaled  = col * Lm1;
    vec3 stepped = floor(scaled) / Lm1;
    vec3 resid   = fract(scaled);                  // 0..1 distance to next step

    // 2-3 — build pattern (depends on u_algorithm)
    int algo = int(clamp(u_algorithm, 0.0, 3.0));
    vec2 pix     = v_uv * u_resolution;
    float cell   = max(1.0, u_grid_size);
    vec2 grid_uv = floor(pix / cell);              // cell coordinates

    vec3 pat;
    if (algo == 0) {
        // ─── circuit modulation (original) ───
        float ch = u_chromatic;
        float pr = circuit_pattern(grid_uv, u_time, 0.0);
        float pg = (ch > 0.001) ? circuit_pattern(grid_uv, u_time, ch * 2.0) : pr;
        float pb = (ch > 0.001) ? circuit_pattern(grid_uv, u_time, ch * 4.0) : pr;
        pat = vec3(pr, pg, pb);
    } else if (algo == 1) {
        // ─── Bayer 4×4 ordered dither (animated: matrix shifts 1px/frame) ───
        // time-varying spatial offset creates the demoscene "flickering dither"
        ivec2 anim = ivec2(int(u_time * 24.0) & 3, int(u_time * 17.0) & 3);
        ivec2 ip = ivec2(grid_uv) + anim;
        float p = bayer4(ip);
        if (u_chromatic > 0.001) {
            int sh = int(u_chromatic * 3.0 + 0.5);
            pat = vec3(p,
                       bayer4(ip + ivec2(sh, 0)),
                       bayer4(ip + ivec2(sh * 2, sh)));
        } else {
            pat = vec3(p);
        }
    } else if (algo == 2) {
        // ─── Bayer 8×8 ordered dither (animated: same trick, larger period) ───
        ivec2 anim = ivec2(int(u_time * 24.0) & 7, int(u_time * 17.0) & 7);
        ivec2 ip = ivec2(grid_uv) + anim;
        float p = bayer8(ip);
        if (u_chromatic > 0.001) {
            int sh = int(u_chromatic * 5.0 + 0.5);
            pat = vec3(p,
                       bayer8(ip + ivec2(sh, 0)),
                       bayer8(ip + ivec2(sh * 2, sh)));
        } else {
            pat = vec3(p);
        }
    } else {
        // ─── Blue noise dither (sample from baked tile) ───
        vec2 noiseUV = (pix / cell) / 64.0;        // 64×64 tile, repeat_xy
        float p = texture(u_blue_noise, noiseUV).r;
        if (u_chromatic > 0.001) {
            vec2 off = vec2(u_chromatic * 0.13, 0.0);
            pat = vec3(p,
                       texture(u_blue_noise, noiseUV + off).r,
                       texture(u_blue_noise, noiseUV + off * 2.0).r);
        } else {
            pat = vec3(p);
        }
    }

    // 4 — jitter
    if (u_jitter > 0.001) {
        vec3 n = vec3(
            hash21(grid_uv +  3.7),
            hash21(grid_uv + 17.4),
            hash21(grid_uv - 23.9)
        ) - 0.5;
        pat = clamp(pat + n * u_jitter, 0.0, 1.0);
    }

    // 5 — polarity
    if (u_polarity > 0.5) pat = 1.0 - pat;

    // 6 — trace mask: cells where pat is near 0.5 are the "wires"
    float t       = clamp(u_trace_thickness, 0.0, 0.5);
    vec3 trace    = smoothstep(vec3(0.5 - t), vec3(0.5 + t), pat);

    // 7 — promote pixel to next level wherever trace allows
    vec3 dith_up  = step(trace, resid);            // resid > trace ? 1 : 0
    vec3 final    = clamp(stepped + dith_up / Lm1, 0.0, 1.0);

    // 8 — colour mode
    vec3 outc;
    if (u_color_mode < 0.5) {
        // mono: luma drives the colour gradient
        float lum = dot(final, vec3(0.299, 0.587, 0.114));
        outc = mix(u_color_a, u_color_b, lum);
    } else if (u_color_mode < 1.5) {
        // per-channel: leave the posterised colours alone
        outc = final;
    } else {
        // duotone: hard binary
        float lum = dot(final, vec3(0.299, 0.587, 0.114));
        outc = lum > 0.5 ? u_color_b : u_color_a;
    }

    out_color = vec4(outc, 1.0);
}
