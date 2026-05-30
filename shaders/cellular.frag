#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  cellular — cellular-automata / reaction-diffusion overlay (feedback)
//
//  Reads the previous output (u_prev) on a coarse cell grid, counts live
//  neighbours and applies a Conway-style life rule, seeded + perturbed by the
//  current frame's luminance.  The living cells are tinted and composited back
//  over the source, so the automaton "grows" along bright image features.
//
//  u_mode:  0 = Life (B3/S23)   1 = decay-diffusion (smoother, organic)
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;     // current source frame  (slot 0)
uniform sampler2D u_prev;    // previous effect output (slot 1)
uniform vec2  u_resolution;
uniform float u_mix;
uniform float u_cell;        // cell size in pixels (2..32)
uniform float u_rate;        // evolution probability per frame (0..1)
uniform float u_seed_thr;    // luma threshold that seeds live cells (0..1)
uniform float u_mode;        // 0 = Life, 1 = decay-diffusion
uniform vec3  u_tint;        // colour of living cells

in  vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float stateAt(vec2 cell, vec2 cellSizeUV) {
    vec2 uv = (cell + 0.5) * cellSizeUV;
    return texture(u_prev, uv).a;     // life stored in alpha
}

void main() {
    vec3  src   = texture(u_tex, v_uv).rgb;
    float luma  = dot(src, vec3(0.299, 0.587, 0.114));

    float cell = max(2.0, u_cell);
    vec2  grid = u_resolution / cell;
    vec2  cellSizeUV = 1.0 / grid;
    vec2  c    = floor(v_uv * grid);

    // count 8 neighbours
    float n = 0.0;
    for (int dy = -1; dy <= 1; ++dy)
        for (int dx = -1; dx <= 1; ++dx) {
            if (dx == 0 && dy == 0) continue;
            n += stateAt(c + vec2(dx, dy), cellSizeUV);
        }

    float self = stateAt(c, cellSizeUV);
    float alive;
    if (u_mode < 0.5) {
        // Conway B3/S23 (neighbour states are 0..1; round the count)
        float nn = floor(n + 0.5);
        bool born = (self < 0.5) && (nn == 3.0);
        bool surv = (self >= 0.5) && (nn == 2.0 || nn == 3.0);
        alive = (born || surv) ? 1.0 : 0.0;
    } else {
        // decay diffusion: blur the neighbourhood, slow fade
        alive = clamp((n / 8.0) * 0.92 + self * 0.55 - 0.18, 0.0, 1.0);
    }

    // seeding: bright image areas spark new cells (gated by rate)
    float spark = step(u_seed_thr, luma) *
                  step(1.0 - clamp(u_rate, 0.0, 1.0), hash21(c + floor(vec2(u_resolution.x, 0.0))));
    alive = max(alive, spark);

    // freeze evolution on cells that didn't roll the rate dice (keeps it stable)
    float roll = hash21(c * 1.7 + 11.0);
    if (roll > clamp(u_rate, 0.0, 1.0)) alive = self;

    vec3 wet = mix(src, u_tint, alive * 0.85);
    fragColor = vec4(mix(src, wet, u_mix), alive);   // store life in alpha for next frame
}
