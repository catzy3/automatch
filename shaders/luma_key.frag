#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  luma_key — perceptually-correct silhouette key with soft edges
//
//  Improvements over a naive RGB→luma:
//    1. PERCEPTUAL LIGHTNESS (Oklab L*) instead of linear-weighted luma.
//       L* is uniformly spaced — equal numerical distance in L* feels
//       like equal perceived brightness step.  Threshold gates feel
//       linear to the eye, not crushed in mids.
//    2. SHADOW LIFT in Oklab (additive on L*, then clamp).
//    3. ACES-style FILMIC TONE-MAPPING with soft shoulder.  Instead of
//       clipping highlights at gain*y > 1, the curve asymptotes to 1
//       — preserves highlight detail.
//    4. POWER (gamma) applied in perceptual space — uniform mid-tone
//       compression / expansion.
//    5. Final value clamped + optional inversion.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_lift;
uniform float u_gain;
uniform float u_gamma;
uniform float u_invert;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

vec3 srgb_to_lin(vec3 c) { return c * c * (c * 0.305306 + 0.682171) + c * 0.012522; }

// Oklab L* component only — much cheaper than full Oklab transform
float oklab_L(vec3 lin) {
    float l = 0.4122214708*lin.r + 0.5363325363*lin.g + 0.0514459929*lin.b;
    float m = 0.2119034982*lin.r + 0.6806995451*lin.g + 0.1073969566*lin.b;
    float s = 0.0883024619*lin.r + 0.2817188376*lin.g + 0.6299787005*lin.b;
    float l_ = pow(max(l, 0.0), 1.0/3.0);
    float m_ = pow(max(m, 0.0), 1.0/3.0);
    float s_ = pow(max(s, 0.0), 1.0/3.0);
    return 0.2104542553*l_ + 0.7936177850*m_ - 0.0040720468*s_;
}

// ACES filmic shoulder approximation:  x · (a x + b) / (x · (c x + d) + e)
// Tuned so f(0)=0, f(1)≈0.85, asymptote at 1.  Soft-clip highlights.
float aces_filmic(float x) {
    float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main() {
    vec3  dry = texture(u_tex, v_uv).rgb;
    vec3  lin = srgb_to_lin(dry);
    float L   = oklab_L(lin);                       // perceptual lightness

    // ─── 1) Shadow lift in perceptual space ───
    L = clamp(L + u_lift, 0.0, 1.0);

    // ─── 4) Mid-tone gamma in perceptual space ───
    L = pow(L, 1.0 / max(u_gamma, 0.05));

    // ─── 3) ACES filmic shoulder applied to L·gain ───
    L = aces_filmic(L * u_gain);

    // ─── 5) Inversion ───
    L = mix(L, 1.0 - L, clamp(u_invert, 0.0, 1.0));

    fragColor = vec4(mix(dry, vec3(L), u_mix), 1.0);
}
