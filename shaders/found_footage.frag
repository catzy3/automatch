#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  found_footage — "cursed videotape" degradation
//
//  Distinct from vhs.frag (NTSC composite simulation).  This shader models
//  the aesthetic of damaged, re-recorded, or partially erased magnetic tape —
//  analog horror / liminal video aesthetic.
//
//  Artifacts:
//    1. Magnetic dropout bands   — horizontal strips where the oxide layer
//                                  failed; signal drops to luminance-only noise
//    2. Colour channel loss      — random screen-space blocks lose one or two
//                                  colour channels entirely (tape oxide dropout)
//    3. Scan-line warp           — horizontal displacement that's sharper and
//                                  more irregular than VHS jitter
//    4. Ghost echo               — faint time-shifted duplicate overlaid at
//                                  low opacity (re-recorded-over tape bleed)
//    5. Luma crush               — periodic brightness collapse (tape squeeze)
//    6. Chroma bleed             — colour smears horizontally into neighbours
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;

// effect controls
uniform float u_dropout_density;   // 0..1  — frequency of dropout bands
uniform float u_dropout_strength;  // 0..1  — how severely bands kill colour
uniform float u_warp_amount;       // 0..0.03 — horizontal warp intensity
uniform float u_ghost_opacity;     // 0..0.5  — echo ghost strength
uniform float u_chroma_bleed;      // 0..8.0  — horizontal chroma smear px
uniform float u_luma_crush;        // 0..1    — periodic brightness collapse

in  vec2 v_uv;
out vec4 out_color;

// ── hash helpers ─────────────────────────────────────────────────────────────

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// ── main ─────────────────────────────────────────────────────────────────────

void main() {
    vec2 uv = v_uv;
    vec2 pix = uv * u_resolution;

    // ── 1. scan-line warp (irregular horizontal displacement) ──
    float warpClock = floor(u_time * 12.0);
    float lineY     = floor(pix.y);
    // two layers: slow drift + sharp per-scanline kick
    float drift = sin(lineY * 0.07 + u_time * 1.3) * 0.4;
    float kick  = (hash11(lineY + warpClock * 7.7) - 0.5) * 2.0
                  * step(0.93, hash11(lineY * 3.1 + warpClock));
    float warp = (drift + kick) * u_warp_amount;
    uv.x = clamp(uv.x + warp, 0.0, 1.0);

    // ── 2. chroma bleed (smear colour right) ──
    float bleed = max(0.0, u_chroma_bleed) / u_resolution.x;
    vec3 col;
    if (bleed > 0.0001) {
        float r = texture(u_tex, uv + vec2(bleed * 2.0, 0.0)).r;
        float g = texture(u_tex, uv + vec2(bleed,       0.0)).g;
        float b = texture(u_tex, uv                         ).b;
        col = vec3(r, g, b);
    } else {
        col = texture(u_tex, uv).rgb;
    }

    // ── 3. ghost echo (faint time-shifted duplicate) ──
    if (u_ghost_opacity > 0.001) {
        float ghostShift = 0.003 + sin(u_time * 0.4) * 0.001;
        vec3  ghost = texture(u_tex, uv + vec2(ghostShift, ghostShift * 0.5)).rgb;
        // desaturate ghost slightly
        float ghostLuma = dot(ghost, vec3(0.299, 0.587, 0.114));
        ghost = mix(ghost, vec3(ghostLuma), 0.6);
        col = mix(col, ghost, u_ghost_opacity);
    }

    // ── 4. magnetic dropout bands ──
    // Bands are a few scanlines tall and appear semi-randomly over time.
    float dropClock = floor(u_time * 6.0);
    float bandSeed  = hash21(vec2(lineY * 0.02, dropClock));
    float inBand    = step(1.0 - u_dropout_density * 0.35, bandSeed);
    if (inBand > 0.5) {
        // inside a dropout band: desaturate + add noise
        float luma = dot(col, vec3(0.299, 0.587, 0.114));
        float noise = hash21(vec2(pix.x * 0.13 + dropClock, lineY)) * 0.25;
        vec3 damaged = vec3(luma + noise) * (1.0 - u_dropout_strength * 0.7);
        // partial colour channel kill
        float killSeed = hash21(vec2(dropClock + 3.7, lineY * 0.01));
        if (killSeed < 0.33)      damaged.r = damaged.r * (1.0 - u_dropout_strength);
        else if (killSeed < 0.66) damaged.g = damaged.g * (1.0 - u_dropout_strength);
        else                      damaged.b = damaged.b * (1.0 - u_dropout_strength);
        col = mix(col, damaged, u_dropout_strength);
    }

    // ── 5. colour-block channel loss (block-level, not scan-line) ──
    float blockSize = 24.0;
    vec2  blockIdx  = floor(pix / blockSize);
    float blockSeed = hash21(blockIdx + floor(u_time * 4.0) * 0.13);
    if (blockSeed > 1.0 - u_dropout_density * 0.2) {
        float ch = hash21(blockIdx * 7.3 + 2.1);
        if      (ch < 0.33) col.rg = vec2(0.0);
        else if (ch < 0.66) col.gb = vec2(0.0);
        else                col.rb = vec2(0.0);
    }

    // ── 6. luma crush (periodic brightness collapse) ──
    if (u_luma_crush > 0.001) {
        float crushWave = sin(u_time * 2.1 + uv.y * 3.0) * 0.5 + 0.5;
        float crushMask = smoothstep(0.85, 1.0, crushWave);
        float luma      = dot(col, vec3(0.299, 0.587, 0.114));
        col = mix(col, col * (1.0 - crushMask * 0.6), u_luma_crush);
    }

    vec3 dry = texture(u_tex, v_uv).rgb;
    out_color = vec4(mix(dry, col, u_mix), 1.0);
}
