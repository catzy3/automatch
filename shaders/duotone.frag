#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  duotone — perceptually-uniform colour mapping in Oklab space
//
//  Real PMS/spot-colour duotone printing process modelled here:
//    1. CONVERT source to Oklab (Björn Ottosson, 2020) — a perceptually
//       uniform colour space.  Equal numerical distance ≈ equal perceived
//       distance, unlike RGB.
//    2. Use Oklab L* (perceptual lightness, NOT linear luma) to index a
//       three-stop colour ramp.  The L axis runs 0..1 with proper
//       perceptual spacing.
//    3. SMOOTHSTEP transitions between stops to avoid hard kinks.
//    4. The target colour at this L* gets its OWN L* substituted with
//       the SOURCE L*, then converted back to RGB.  This is the proper
//       way to do "luma preservation": preserve the perceptual lightness,
//       not the linear luma.  Avoids the muddy mid-tones you get from
//       naive RGB lerps.
//    5. The strength parameter smoothly biases between grayscale-of-L*
//       and the fully retoned colour — also in Oklab space, so the
//       desaturated state has the right perceptual brightness too.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec3  u_accent;
uniform vec3  u_shadow;
uniform float u_midpoint;
uniform float u_strength;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float ssmooth(float a, float b, float x) {
    float t = clamp((x - a) / max(1e-4, b - a), 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}

// ─── sRGB ↔ linear ───
vec3 srgb_to_lin(vec3 c) { return c * c * (c * 0.305306 + 0.682171) + c * 0.012522; }
vec3 lin_to_srgb(vec3 c) {
    vec3 s = clamp(c, 0.0, 1.0);
    return mix(s * 12.92, 1.055 * pow(s, vec3(1.0/2.4)) - 0.055, step(0.0031308, s));
}

// ─── linear RGB ↔ Oklab (Björn Ottosson) ───
vec3 rgb_to_oklab(vec3 c) {
    float l = 0.4122214708 * c.r + 0.5363325363 * c.g + 0.0514459929 * c.b;
    float m = 0.2119034982 * c.r + 0.6806995451 * c.g + 0.1073969566 * c.b;
    float s = 0.0883024619 * c.r + 0.2817188376 * c.g + 0.6299787005 * c.b;
    float l_ = pow(max(l, 0.0), 1.0/3.0);
    float m_ = pow(max(m, 0.0), 1.0/3.0);
    float s_ = pow(max(s, 0.0), 1.0/3.0);
    return vec3(
        0.2104542553*l_ + 0.7936177850*m_ - 0.0040720468*s_,
        1.9779984951*l_ - 2.4285922050*m_ + 0.4505937099*s_,
        0.0259040371*l_ + 0.7827717662*m_ - 0.8086757660*s_);
}

vec3 oklab_to_rgb(vec3 lab) {
    float l_ = lab.x + 0.3963377774*lab.y + 0.2158037573*lab.z;
    float m_ = lab.x - 0.1055613458*lab.y - 0.0638541728*lab.z;
    float s_ = lab.x - 0.0894841775*lab.y - 1.2914855480*lab.z;
    float l = l_*l_*l_;
    float m = m_*m_*m_;
    float s = s_*s_*s_;
    return vec3(
         4.0767416621*l - 3.3077115913*m + 0.2309699292*s,
        -1.2684380046*l + 2.6097574011*m - 0.3413193965*s,
        -0.0041960863*l - 0.7034186147*m + 1.7076147010*s);
}

void main() {
    vec3 dry   = texture(u_tex, v_uv).rgb;
    vec3 lin   = srgb_to_lin(dry);
    vec3 lab   = rgb_to_oklab(lin);
    float L    = lab.x;                                  // perceptual lightness

    // ─── three-stop colour ramp keyed by perceptual L* ───
    vec3 lab_shadow    = rgb_to_oklab(srgb_to_lin(u_shadow));
    vec3 lab_accent    = rgb_to_oklab(srgb_to_lin(u_accent));
    vec3 lab_highlight = rgb_to_oklab(srgb_to_lin(vec3(1.0)));

    vec3 lab_target;
    if (L < u_midpoint) {
        lab_target = mix(lab_shadow, lab_accent, ssmooth(0.0, u_midpoint, L));
    } else {
        lab_target = mix(lab_accent, lab_highlight, ssmooth(u_midpoint, 1.0, L));
    }

    // ─── preserve original L*, take only the chroma (a*,b*) ───
    lab_target.x = L;

    // ─── strength: smoothly bias between grayscale and retoned ───
    float s = ssmooth(0.0, 1.0, u_strength);
    vec3 lab_out = mix(vec3(L, 0.0, 0.0), lab_target, s);

    vec3 graded = lin_to_srgb(oklab_to_rgb(lab_out));
    fragColor   = vec4(mix(dry, graded, u_mix), 1.0);
}
