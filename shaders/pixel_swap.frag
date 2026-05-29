#version 330

// Pixel swap: each pixel can pull its color from a random nearby pixel.
//
// Knobs:
//   u_threshold  — fraction of pixels that swap at all (low → sparse, high → dense)
//   u_radius     — how far away the swap partner can be
//   u_chaos      — magnitude variance: at 0 swaps go a fixed distance, at 1 every
//                  pixel picks its own distance
//   u_directional — 0 = isotropic random direction, 1 = locked to horizontal
//                   (= row-shuffle, looks like data corruption)
//   u_temporal   — 0 = pattern is frozen, 1 = re-rolls every frame (sizzle)
//
// Stateless — does not need a previous-output texture.

uniform sampler2D u_tex;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_threshold;
uniform float u_radius;
uniform float u_chaos;
uniform float u_directional;
uniform float u_temporal;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float hash31(vec3 p3) {
    p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec2 px = 1.0 / u_resolution;
    vec3 dry = texture(u_tex, v_uv).rgb;

    // temporal seed: 0 → static, larger → re-rolls quickly
    float seed = floor(u_time * mix(0.5, 60.0, u_temporal));

    // tiles: granularity of the swap pattern (small tiles → fine noise)
    vec2 cell = floor(v_uv * u_resolution / max(1.0, 2.0 + 8.0 * (1.0 - u_chaos)));

    // does this cell participate?
    float roll = hash21(cell + seed * 1.7);
    float swap = step(1.0 - u_threshold, roll);

    // direction: isotropic at low u_directional, locked to ±x at high
    float ang  = hash31(vec3(cell, seed)) * 6.28318;
    vec2  iso  = vec2(cos(ang), sin(ang));
    vec2  hor  = vec2(sign(hash21(cell + seed + 0.31) - 0.5), 0.0);
    vec2  dir  = normalize(mix(iso, hor, u_directional) + 1e-6);

    // magnitude: bounded by u_radius, modulated by u_chaos
    float baseMag = 4.0 + 40.0 * u_radius;
    float mag = baseMag * mix(0.6, hash21(cell + seed * 2.1) * 1.6, u_chaos);

    vec2 offset = dir * px * mag * swap;
    vec2 sampUV = clamp(v_uv + offset, vec2(0.0), vec2(1.0));
    vec3 wet = texture(u_tex, sampUV).rgb;

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
