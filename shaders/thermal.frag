#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  thermal — false-colour infrared look
//
//  Maps source luminance through a thermal-camera colour ramp.  Several
//  palettes (infrared / ironbow / rainbow / arctic) approximate the LUTs used
//  by FLIR-style imagers.  Gain + gamma reshape the response curve; levels
//  posterises the gradient into discrete temperature bands.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_palette;   // 0=infrared 1=ironbow 2=rainbow 3=arctic 4=LYGIA heatmap
uniform float u_gain;      // pre-multiply luma (0.2..3)
uniform float u_gamma;     // luma gamma (0.2..4)
uniform float u_levels;    // posterise bands (0 = smooth)

in  vec2 v_uv;
out vec4 fragColor;

vec3 lerp3(vec3 a, vec3 b, float t) { return mix(a, b, clamp(t, 0.0, 1.0)); }

// piecewise colour ramp over 5 stops
vec3 ramp5(float t, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4) {
    t = clamp(t, 0.0, 1.0) * 4.0;
    if (t < 1.0) return lerp3(c0, c1, t);
    if (t < 2.0) return lerp3(c1, c2, t - 1.0);
    if (t < 3.0) return lerp3(c2, c3, t - 2.0);
    return lerp3(c3, c4, t - 3.0);
}

// LYGIA color/palette/heatmap — compact parabolic colormap
vec3 heatmap(float v) {
    vec3 r = v * 2.1 - vec3(1.8, 1.14, 0.3);
    return clamp(1.0 - r * r, 0.0, 1.0);
}

vec3 palette(float t, int p) {
    if (p == 4) {            // LYGIA heatmap
        return heatmap(clamp(t, 0.0, 1.0));
    } else if (p == 1) {     // ironbow
        return ramp5(t, vec3(0.0,0.0,0.0), vec3(0.25,0.0,0.35),
                        vec3(0.75,0.2,0.25), vec3(1.0,0.6,0.0), vec3(1.0,1.0,0.85));
    } else if (p == 2) {     // rainbow
        return ramp5(t, vec3(0.0,0.0,0.4), vec3(0.0,0.7,0.9),
                        vec3(0.1,0.9,0.2), vec3(1.0,0.85,0.0), vec3(1.0,0.1,0.1));
    } else if (p == 3) {     // arctic (cold)
        return ramp5(t, vec3(0.0,0.0,0.05), vec3(0.0,0.1,0.4),
                        vec3(0.0,0.5,0.7), vec3(0.5,0.85,0.95), vec3(1.0,1.0,1.0));
    }
    // 0 = infrared (default)
    return ramp5(t, vec3(0.0,0.0,0.0), vec3(0.15,0.0,0.4),
                    vec3(0.8,0.0,0.3), vec3(1.0,0.55,0.0), vec3(1.0,1.0,0.9));
}

void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    float luma = dot(src, vec3(0.299, 0.587, 0.114));
    luma = pow(clamp(luma * u_gain, 0.0, 1.0), 1.0 / max(0.05, u_gamma));
    if (u_levels >= 2.0) {
        float n = u_levels;
        luma = floor(luma * n + 0.5) / n;
    }
    vec3 wet = palette(luma, int(u_palette + 0.5));
    fragColor = vec4(mix(src, wet, u_mix), 1.0);
}
