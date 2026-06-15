#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  distort — procedural displacement-map warp  (tooooools-style)
//
//  Classic displacement-map effect: instead of loading an external map image,
//  a grayscale field is generated procedurally (value-noise fBM, optionally
//  animated by time).  The field is pre-processed exactly the way you'd tweak
//  a distortion-map bitmap — blur, grain, gamma, black-point, white-point —
//  then its value drives a per-pixel UV offset:
//
//      d  = (map - 0.5) * 2            // remap [0,1] → [-1,1]
//      d *= gate(map, threshold)       // only displace bright-enough areas
//      uv += d * vec2(xShift, yShift)  // shift source lookup
//
//  Two slightly decorrelated fields drive X and Y so the warp isn't a pure
//  diagonal smear.  The grayscale "map" you'd see on the site corresponds to
//  the X field after pre-processing.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_time;
uniform vec2  u_resolution;

uniform float u_xshift;      // X displacement strength
uniform float u_yshift;      // Y displacement strength
uniform float u_threshold;   // displace only where map > threshold (0 = all)
uniform float u_scale;       // procedural map frequency (pattern size)
uniform float u_speed;       // map animation speed (0 = frozen)
uniform float u_blur;        // soften the map (0..1)
uniform float u_grain;       // fine noise added to the map (0..1)
uniform float u_gamma;       // map gamma (0.2..4)
uniform float u_black;       // map black point (0..1)
uniform float u_white;       // map white point (0..1)
uniform float u_noise_mode;  // 0 = value-noise fBM, 1 = simplex fBM (LYGIA)
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// value noise in 0..1
float noise2d(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

// fractional Brownian motion, 4 octaves, output in 0..1
float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    float norm = 0.0;
    for (int i = 0; i < 4; ++i) {
        v += a * noise2d(p);
        norm += a;
        p *= 2.0;
        a *= 0.5;
    }
    return v / max(1e-4, norm);
}

// ── Simplex noise (Ashima/Gustavson — the impl used by LYGIA/generative) ──
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec2 mod289(vec2 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec3 permute(vec3 x) { return mod289(((x * 34.0) + 1.0) * x); }

float snoise(in vec2 v) {
    const vec4 C = vec4(0.211324865405187, 0.366025403784439,
                       -0.577350269189626, 0.024390243902439);
    vec2 i  = floor(v + dot(v, C.yy));
    vec2 x0 = v - i + dot(i, C.xx);
    vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
    vec4 x12 = x0.xyxy + C.xxzz;
    x12.xy -= i1;
    i = mod289(i);
    vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0))
                            + i.x + vec3(0.0, i1.x, 1.0));
    vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy),
                            dot(x12.zw, x12.zw)), 0.0);
    m = m * m; m = m * m;
    vec3 x = 2.0 * fract(p * C.www) - 1.0;
    vec3 h = abs(x) - 0.5;
    vec3 ox = floor(x + 0.5);
    vec3 a0 = x - ox;
    m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
    vec3 g;
    g.x  = a0.x  * x0.x  + h.x  * x0.y;
    g.yz = a0.yz * x12.xz + h.yz * x12.yw;
    return 130.0 * dot(m, g);                // ≈ [-1, 1]
}

// simplex fBM (4 octaves, LYGIA defaults: gain 0.5, lacunarity 2.0) → 0..1
float fbm_s(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    float norm = 0.0;
    for (int i = 0; i < 4; ++i) {
        v += a * snoise(p);
        norm += a;
        p *= 2.0;
        a *= 0.5;
    }
    return clamp(0.5 + 0.5 * (v / max(1e-4, norm)), 0.0, 1.0);
}

// dispatch on u_noise_mode so the user can pick map quality
float fbm_any(vec2 p) {
    return (u_noise_mode > 0.5) ? fbm_s(p) : fbm(p);
}

// One pre-processed scalar map sample at uv (seed offsets the field so X / Y
// channels can be decorrelated).  Returns a value in 0..1.
float field(vec2 uv, float seed) {
    float fr = max(0.25, u_scale);
    vec2  p  = uv * fr + vec2(seed, seed * 1.7);
    vec2  drift = vec2(u_time * u_speed * 0.5, u_time * u_speed * 0.37);

    // sharp + soft (half-frequency) → blur mixes toward the softer one
    float sharp = fbm_any(p + drift);
    float soft  = fbm_any(p * 0.5 + drift * 0.5);
    float m = mix(sharp, soft, clamp(u_blur, 0.0, 1.0));

    // black / white point remap
    float lo = clamp(u_black, 0.0, 0.99);
    float hi = clamp(u_white, lo + 0.01, 1.0);
    m = clamp((m - lo) / (hi - lo), 0.0, 1.0);

    // gamma
    m = pow(m, 1.0 / max(0.05, u_gamma));

    // grain — fine per-texel hash noise
    if (u_grain > 1e-4) {
        float g = hash21(floor(uv * u_resolution) + seed) - 0.5;
        m = clamp(m + g * u_grain, 0.0, 1.0);
    }
    return m;
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float mx = field(v_uv, 0.0);
    float my = field(v_uv, 37.0);

    // threshold gate (soft).  threshold≈0 → always displace.
    float th = clamp(u_threshold, 0.0, 1.0);
    float gx = (th <= 0.001) ? 1.0 : smoothstep(th - 0.06, th + 0.06, mx);
    float gy = (th <= 0.001) ? 1.0 : smoothstep(th - 0.06, th + 0.06, my);

    float dx = (mx - 0.5) * 2.0 * gx;
    float dy = (my - 0.5) * 2.0 * gy;

    // 0.12 = max displacement as fraction of the frame at full strength
    vec2 off = vec2(dx * u_xshift, dy * u_yshift) * 0.12;
    vec2 uv2 = clamp(v_uv + off, 0.0, 1.0);

    vec3 wet = texture(u_tex, uv2).rgb;
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
