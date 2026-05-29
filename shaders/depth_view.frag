#version 330

// Visualise the depth map. Three modes via u_mode:
//   0 = grayscale (white near, black far)
//   1 = heatmap (blue far → cyan → yellow → red near)
//   2 = blend with current frame (depth-overlay)

uniform sampler2D u_tex;
uniform sampler2D u_depth;
uniform float u_mode;
uniform float u_gain;
uniform float u_offset;
uniform float u_invert;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

vec3 heatmap(float t) {
    t = clamp(t, 0.0, 1.0);
    vec3 c1 = vec3(0.05, 0.10, 0.55);   // deep blue
    vec3 c2 = vec3(0.20, 0.80, 0.95);   // cyan
    vec3 c3 = vec3(0.95, 0.85, 0.20);   // yellow
    vec3 c4 = vec3(0.95, 0.25, 0.20);   // red
    if (t < 0.33) return mix(c1, c2, t / 0.33);
    if (t < 0.66) return mix(c2, c3, (t - 0.33) / 0.33);
    return mix(c3, c4, (t - 0.66) / 0.34);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    float d = texture(u_depth, v_uv).r;
    d = clamp((d + u_offset) * u_gain, 0.0, 1.0);
    if (u_invert > 0.5) d = 1.0 - d;

    vec3 wet;
    int m = int(clamp(u_mode, 0.0, 2.0));
    if (m == 0) {
        wet = vec3(d);
    } else if (m == 1) {
        wet = heatmap(d);
    } else {
        wet = mix(dry, heatmap(d), 0.45);
    }
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
