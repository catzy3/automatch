#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  elastic_grid — grid mesh warp with elastic interpolation
//
//  The frame is divided into a lattice of cells.  Each lattice node gets a
//  pseudo-random, time-animated offset.  Per-pixel displacement is a smooth
//  bilinear blend of the four surrounding node offsets, so the warp behaves
//  like an elastic mesh (no hard cell seams).  u_jitter adds per-cell shear.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_time;
uniform float u_mix;
uniform float u_grid;     // nodes across (2..32)
uniform float u_strength; // displacement amount (0..1)
uniform float u_speed;    // animation speed (0..2)
uniform float u_jitter;   // per-cell shear / chaos (0..1)
uniform float u_noise_mode; // 0 = hash sin/cos, 1 = simplex flow (LYGIA)

in  vec2 v_uv;
out vec4 fragColor;

vec2 hash22(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.xx + p3.yz) * p3.zy) * 2.0 - 1.0;   // [-1,1]^2
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

// animated node offset — two flavours, picked by u_noise_mode
vec2 nodeOffset(vec2 node) {
    float t = u_time * u_speed;
    float amp = 0.5 + 0.5 * u_jitter;
    if (u_noise_mode > 0.5) {
        // simplex flow: two decorrelated noise fields drifting over time → a
        // smoother, more organic mesh than the per-node sine wobble
        vec2 q = node * 0.6;
        float nx = snoise(q + vec2(t * 0.30, 0.0));
        float ny = snoise(q + vec2(0.0, t * 0.27) + 31.7);
        return vec2(nx, ny) * amp;
    }
    vec2 h = hash22(node);
    return vec2(sin(t + h.x * 6.2831 + node.x),
                cos(t * 1.13 + h.y * 6.2831 + node.y)) * amp;
}

void main() {
    float g = max(2.0, u_grid);
    vec2  gp = v_uv * g;
    vec2  i  = floor(gp);
    vec2  f  = gp - i;
    vec2  w  = f * f * (3.0 - 2.0 * f);   // smoothstep weights → elastic

    vec2 o00 = nodeOffset(i + vec2(0.0, 0.0));
    vec2 o10 = nodeOffset(i + vec2(1.0, 0.0));
    vec2 o01 = nodeOffset(i + vec2(0.0, 1.0));
    vec2 o11 = nodeOffset(i + vec2(1.0, 1.0));

    vec2 ox = mix(mix(o00, o10, w.x), mix(o01, o11, w.x), w.y);
    vec2 disp = ox * (u_strength * 0.12) / g;   // scale by grid so cells stay coherent

    vec2 uv2 = clamp(v_uv + disp, 0.0, 1.0);
    vec3 wet = texture(u_tex, uv2).rgb;
    vec3 dry = texture(u_tex, v_uv).rgb;
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
