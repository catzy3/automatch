#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  swirl.frag  —  radial swirl / twist warp
//
//  Classic swirl: pixels within u_radius of the centre are rotated by an
//  angle that falls off smoothly from the centre outward.  The falloff uses
//  smoothstep so the boundary of the effect is C1-continuous (no hard ring).
//
//  Pipeline:
//    1. remap UV to centred, aspect-corrected space
//    2. compute dist = distance from centre, normalised by u_radius * min_dim/2
//    3. twist angle = effective_strength * smoothstep(1.0, 0.0, dist)
//       — pixels at the centre rotate by the full strength
//       — pixels beyond radius rotate by 0
//    4. rotate (dx,dy) by twist, reconstruct source UV
//    5. sample texture at rotated UV, mix dry/wet
//
//  u_animate > 0 modulates effective_strength by sin(u_time), producing a
//  continuous breathing swirl animation.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;        // 0..1   — dry/wet blend

uniform float u_strength;   // 0..10  — peak rotation in radians
uniform float u_radius;     // 0.1..1 — effect radius as fraction of min(w,h)/2
uniform float u_cx;         // 0..1   — centre X in UV space (default 0.5)
uniform float u_cy;         // 0..1   — centre Y in UV space (default 0.5)
uniform float u_animate;    // 0..1   — animate strength with sin(u_time)

in  vec2 v_uv;
out vec4 out_color;

// ─────────────────────────────────────────── 2-D rotation

vec2 rotate2d(vec2 p, float angle) {
    float s = sin(angle);
    float c = cos(angle);
    return vec2(p.x * c - p.y * s,
                p.x * s + p.y * c);
}

// ─────────────────────────────────────────────────────────────────────── main

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // ── 1. move to centred, aspect-corrected coordinates ──────────────────
    // Aspect correction ensures the swirl zone is a circle on screen,
    // not an ellipse when width ≠ height.
    float aspect = u_resolution.x / max(u_resolution.y, 1.0);
    vec2  centre = vec2(clamp(u_cx, 0.0, 1.0),
                        clamp(u_cy, 0.0, 1.0));
    vec2  delta  = v_uv - centre;
    delta.x     *= aspect;           // scale X so 1 unit = same screen size in Y

    // ── 2. normalised distance from centre ────────────────────────────────
    // u_radius is a fraction of min(w,h)/2, which in aspect-corrected UV
    // units equals u_radius directly (since we are normalising to 0.5 half-
    // width of the shorter axis).
    float radius  = max(u_radius, 0.001);
    float dist    = length(delta) / radius;   // 0 at centre, 1 at boundary

    // ── 3. effective strength (optionally animated) ────────────────────────
    float strength;
    if (u_animate > 0.5) {
        // Full animation: strength oscillates between -u_strength and +u_strength
        strength = u_strength * sin(u_time);
    } else if (u_animate > 0.001) {
        // Partial blend: mix static with animated
        float anim = u_strength * sin(u_time);
        strength   = mix(u_strength, anim, u_animate);
    } else {
        strength = u_strength;
    }

    // ── 4. twist angle with smooth falloff ────────────────────────────────
    // smoothstep(1.0, 0.0, dist): 1 at dist=0 (centre), 0 at dist=1 (edge),
    // smooth C1 curve between — no hard discontinuity at the swirl boundary.
    float falloff = smoothstep(1.0, 0.0, dist);
    float twist   = strength * falloff;      // radians; 0 outside radius

    // ── 5. rotate delta, reconstruct UV, sample ───────────────────────────
    vec2 warped_delta  = rotate2d(delta, twist);
    warped_delta.x    /= aspect;             // undo aspect correction before UV lookup

    vec2 warped_uv = centre + warped_delta;

    // Clamp to [0,1] — avoids border sampling artefacts on edge pixels.
    // (Edge-clamp is typical for this effect; consider a wrap mode parameter
    //  if the project later needs tiling sources.)
    warped_uv = clamp(warped_uv, 0.0, 1.0);

    vec3 wet = texture(u_tex, warped_uv).rgb;

    out_color = vec4(mix(dry, wet, u_mix), 1.0);
}
