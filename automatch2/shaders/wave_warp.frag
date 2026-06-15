#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  wave_warp — physically-based displacement field with curl noise + fBM.
//  Scalarised port of the v1 shader: the three wave layers keep v1's tuned
//  per-layer ratios (amp/freq/phase/speed/shape) baked as constants, and the
//  host exposes a small master set (u_amp / u_freq / u_angle / u_polar /
//  u_chromatic) instead of 21 array sliders. The curl-noise fBM domain-warp
//  path (layer 3, shape=4) is the marquee turbulent-flow look.
// ─────────────────────────────────────────────────────────────────────────────
uniform sampler2D u_tex;
uniform float u_time;
uniform float u_amp;       // master amplitude scale (×)
uniform float u_freq;      // master frequency scale (×)
uniform float u_angle;     // displacement direction, 0..1 → 0..2π
uniform float u_polar;     // 0 = cartesian drive, 1 = polar (radial) drive
uniform float u_chromatic; // per-channel chromatic dispersion
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

// v1 per-layer defaults (wave_amp/freq/phase/speed/shape 1..3), baked.
const float A[3]  = float[3](0.30, 0.20, 0.10);
const float F[3]  = float[3](18.0, 47.0, 91.0);
const float PH[3] = float[3](0.0,  1.20, 2.40);
const float SP[3] = float[3](1.0,  1.70, 0.50);
const float SH[3] = float[3](0.0,  0.0,  4.0);  // 0=sin 1=tri 2=saw 3=square 4=curl-noise

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float noise2d(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y) * 2.0 - 1.0;
}
float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 4; ++i) { v += a * noise2d(p); p *= 2.0; a *= 0.5; }
    return v;
}
// Curl of an fBM scalar potential → divergence-free 2D vector (incompressible flow).
vec2 curl_fbm(vec2 p, float t) {
    float eps = 0.05;
    float n_p = fbm(p + vec2(t * 0.3, t * 0.2));
    float n_x = fbm(p + vec2(eps + t * 0.3, t * 0.2));
    float n_y = fbm(p + vec2(t * 0.3, eps + t * 0.2));
    float dpdx = (n_x - n_p) / eps;
    float dpdy = (n_y - n_p) / eps;
    return vec2(dpdy, -dpdx);
}
float wave_layer(int idx, float t, vec2 uv2d) {
    float shape = SH[idx];
    float val;
    float twoPi = 6.28318530;
    if (shape < 0.5) {
        val = sin(t * twoPi);
    } else if (shape < 1.5) {
        float p = fract(t);
        val = abs(2.0 * p - 1.0) * 2.0 - 1.0;
    } else if (shape < 2.5) {
        float p = fract(t);
        val = p * 2.0 - 1.0;
    } else if (shape < 3.5) {
        val = sin(t * twoPi) > 0.0 ? 1.0 : -1.0;
    } else {
        vec2 c1 = curl_fbm(uv2d * 4.0, t * 0.5);
        vec2 c2 = curl_fbm(uv2d * 4.0 + c1 * 1.5, t * 0.5);  // domain warp
        val = dot(c2, vec2(0.7, 0.3)) * 1.4;
    }
    return val * A[idx] * u_amp;
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float ang  = u_angle * 6.28318530;
    vec2  dir  = vec2(cos(ang), sin(ang));
    vec2  perp = vec2(-dir.y, dir.x);

    float drive_cart  = dot(v_uv, perp);
    vec2  c           = v_uv - 0.5;
    float drive_polar = length(c);
    float drive = mix(drive_cart, drive_polar, u_polar);

    // domain warping: layer 0 modulates the drive of layers 1 & 2
    float w0 = wave_layer(0, drive * F[0] * u_freq + PH[0] + u_time * SP[0], v_uv);
    float drive2 = drive + w0 * 0.12;
    float w1 = wave_layer(1, drive2 * F[1] * u_freq + PH[1] + u_time * SP[1], v_uv);
    float w2 = wave_layer(2, drive2 * F[2] * u_freq + PH[2] + u_time * SP[2], v_uv);

    float displace = (w0 + w1 + w2) * 0.05;
    vec2  warpUV   = v_uv + dir * displace;

    // per-channel chromatic dispersion
    vec2 chOff = dir * (u_chromatic * displace * 1.2);
    float chPhaseR =  u_chromatic * 0.05;
    float chPhaseB = -u_chromatic * 0.05;
    vec3 wet;
    wet.r = texture(u_tex, warpUV + chOff + dir * chPhaseR * displace).r;
    wet.g = texture(u_tex, warpUV).g;
    wet.b = texture(u_tex, warpUV - chOff + dir * chPhaseB * displace).b;

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
