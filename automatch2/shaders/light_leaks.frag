#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // leak strength

// Cinematic light leaks — soft warm blobs drifting in from the edges,
// composited with a screen blend.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 p = v_uv;
    float leak = 0.0;
    leak += smoothstep(0.55, 0.0, length(p - vec2(0.08 + 0.25 * sin(u_time * 0.30), 0.55)));
    leak += 0.7 * smoothstep(0.70, 0.0, length(p - vec2(0.96, 0.25 + 0.30 * cos(u_time * 0.22))));
    leak += 0.5 * smoothstep(0.45, 0.0, length(p - vec2(0.5 + 0.4 * sin(u_time * 0.17), 1.02)));
    vec3 warm = vec3(1.0, 0.55, 0.22);
    vec3 cool = vec3(0.3, 0.5, 1.0);
    vec3 leak_col = leak * mix(warm, cool, 0.5 + 0.5 * sin(u_time * 0.4)) * 0.9 * u_p0;
    vec3 col = 1.0 - (1.0 - src) * (1.0 - leak_col); // screen blend
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
