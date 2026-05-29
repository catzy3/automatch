#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  dual_tone — three-stop split-toning in Oklab perceptual colour space
//
//  Cinema-grade split-toning physics:
//    1. CONVERT source to Oklab (perceptually uniform).
//    2. Build a three-stop ramp (shadow → mid → highlight → white) keyed
//       by perceptual L*, with the u_split parameter controlling where
//       the highlight peak sits.
//    3. SMOOTHSTEP cubic interpolation at each pivot — avoids the hard
//       banding that linear lerps produce.
//    4. PRESERVE L*: replace source colour's a* / b* (chroma axes) with
//       the ramp's, but keep the source's L* (perceptual lightness).
//       This is the proper "luma preservation" — deep blacks stay deep,
//       bright highlights stay bright, only the colour cast changes.
//    5. STRENGTH curve also smoothed — biases between full retone and
//       perceptually-correct grayscale of L*.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec3  u_shadow;
uniform vec3  u_mid;
uniform vec3  u_highlight;
uniform float u_split;
uniform float u_strength;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float ssmooth(float a, float b, float x) {
    float t = clamp((x - a) / max(1e-4, b - a), 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}

vec3 srgb_to_lin(vec3 c) { return c * c * (c * 0.305306 + 0.682171) + c * 0.012522; }
vec3 lin_to_srgb(vec3 c) {
    vec3 s = clamp(c, 0.0, 1.0);
    return mix(s * 12.92, 1.055 * pow(s, vec3(1.0/2.4)) - 0.055, step(0.0031308, s));
}

vec3 rgb_to_oklab(vec3 c) {
    float l = 0.4122214708*c.r + 0.5363325363*c.g + 0.0514459929*c.b;
    float m = 0.2119034982*c.r + 0.6806995451*c.g + 0.1073969566*c.b;
    float s = 0.0883024619*c.r + 0.2817188376*c.g + 0.6299787005*c.b;
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
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec3 lin = srgb_to_lin(dry);
    vec3 lab = rgb_to_oklab(lin);
    float L  = lab.x;

    // ─── three Oklab stops ───
    vec3 lab_sh = rgb_to_oklab(srgb_to_lin(u_shadow));
    vec3 lab_md = rgb_to_oklab(srgb_to_lin(u_mid));
    vec3 lab_hi = rgb_to_oklab(srgb_to_lin(u_highlight));
    vec3 lab_w  = rgb_to_oklab(srgb_to_lin(vec3(1.0)));

    // u_split pivots: shadow→mid at 0.5*split, mid→highlight at split.
    float p1 = u_split * 0.5;
    float p2 = u_split;

    vec3 lab_target;
    if (L < p1)       lab_target = mix(lab_sh, lab_md, ssmooth(0.0, p1, L));
    else if (L < p2)  lab_target = mix(lab_md, lab_hi, ssmooth(p1, p2, L));
    else              lab_target = mix(lab_hi, lab_w,  ssmooth(p2, 1.0, L));

    // ─── preserve source L*, only retone chroma ───
    lab_target.x = L;

    // ─── smooth strength curve in Oklab ───
    float s = ssmooth(0.0, 1.0, u_strength);
    vec3 lab_out = mix(vec3(L, 0.0, 0.0), lab_target, s);

    vec3 graded = lin_to_srgb(oklab_to_rgb(lab_out));
    fragColor   = vec4(mix(dry, graded, u_mix), 1.0);
}
