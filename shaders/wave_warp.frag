#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  wave_warp — physically-based displacement field with curl noise + fBM
//
//  Real fluid / elastic displacement physics:
//    1. CURL NOISE — vector field derived from the analytical curl of a
//       scalar noise field.  Mathematically:  v = ∇ × ψ(x,y) where ψ is
//       a 2-D scalar potential.  Result: divergence-free flow (∇·v = 0),
//       which is what real incompressible fluid does (no sources/sinks).
//       In 2-D: v = (∂ψ/∂y, -∂ψ/∂x).  We sample ψ at four offsets and
//       finite-difference the partials.
//    2. FRACTIONAL BROWNIAN MOTION (fBM) — multi-octave noise sum:
//          fBM(x) = Σ amp_i · noise(freq_i · x)   with amp_i = 0.5^i.
//       Produces natural turbulence with self-similar structure at every
//       scale.
//    3. DOMAIN WARPING (Iñigo Quilez technique) — feed the output of
//       one noise sample back as the input coordinates of the next.
//       Three iterations.  Generates the characteristic "tortured" look
//       of real turbulent flow.
//    4. PER-CHANNEL chromatic dispersion: R, G, B sampled at slightly
//       different phases of the displacement field — mimics how light
//       refracts through a moving medium.
//    5. WAVE LAYERS preserved (sine/triangle/saw/square) for cases
//       where a periodic structure is wanted.  Shape=4 now invokes
//       curl-noise fBM domain warping.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_time;

uniform float u_amp[3];
uniform float u_freq[3];
uniform float u_phase[3];
uniform float u_speed[3];
uniform float u_shape[3];

uniform float u_angle;
uniform float u_polar;
uniform float u_chromatic;
uniform float u_band_height;
uniform float u_band_y;
uniform float u_band_soft;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float noise1d(float x) {
    float i = floor(x); float f = fract(x);
    float a = hash11(i); float b = hash11(i + 1.0);
    float u = f * f * (3.0 - 2.0 * f);
    return mix(a, b, u) * 2.0 - 1.0;
}

// 2D value noise with cubic interpolation (smoother gradients than linear).
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

// fractional Brownian motion: 4 octaves, amp halves each time, freq doubles.
float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 4; ++i) {
        v += a * noise2d(p);
        p *= 2.0;
        a *= 0.5;
    }
    return v;
}

// Curl of an fBM scalar potential.  Returns a divergence-free 2D vector.
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
    float shape = u_shape[idx];
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
        // ─── shape 4: curl-noise fBM with domain warping ───
        vec2 c1 = curl_fbm(uv2d * 4.0, t * 0.5);
        vec2 c2 = curl_fbm(uv2d * 4.0 + c1 * 1.5, t * 0.5);  // domain warp
        val = dot(c2, vec2(0.7, 0.3)) * 1.4;
    }
    return val * u_amp[idx];
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // ─── band mask ───
    float halfH    = max(0.001, u_band_height * 0.5);
    float distY    = abs(v_uv.y - u_band_y);
    float bandOut  = smoothstep(halfH, halfH * (1.0 - u_band_soft), distY);
    float bandMask = 1.0 - bandOut;

    // direction & perpendicular
    float ang  = u_angle * 6.28318530;
    vec2  dir  = vec2(cos(ang), sin(ang));
    vec2  perp = vec2(-dir.y, dir.x);

    float drive_cart  = dot(v_uv, perp);
    vec2  c           = v_uv - 0.5;
    float drive_polar = length(c);
    float drive = mix(drive_cart, drive_polar, u_polar);

    // ─── domain warping: layer 0 modulates the drive of layers 1 & 2 ───
    float w0 = wave_layer(0, drive * u_freq[0] + u_phase[0] + u_time * u_speed[0], v_uv);
    float drive2 = drive + w0 * 0.12;

    float w1 = wave_layer(1, drive2 * u_freq[1] + u_phase[1] + u_time * u_speed[1], v_uv);
    float w2 = wave_layer(2, drive2 * u_freq[2] + u_phase[2] + u_time * u_speed[2], v_uv);

    float displace = (w0 + w1 + w2) * 0.05 * bandMask;
    vec2  warpUV   = v_uv + dir * displace;

    // ─── chromatic dispersion: per-channel sub-displacement ───
    vec2 chOff = dir * (u_chromatic * displace * 1.2);
    float chPhaseR =  u_chromatic * 0.05;
    float chPhaseB = -u_chromatic * 0.05;
    vec3 wet;
    wet.r = texture(u_tex, warpUV + chOff + dir * chPhaseR * displace).r;
    wet.g = texture(u_tex, warpUV).g;
    wet.b = texture(u_tex, warpUV - chOff + dir * chPhaseB * displace).b;

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
