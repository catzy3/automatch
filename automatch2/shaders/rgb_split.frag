#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // offset amount
uniform float u_p1; // animation speed

// Horizontal chromatic channel split, gently animated.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    float amt = 6.0 * u_p0 / max(u_resolution.x, 1.0);
    float o = amt * (0.4 + 0.6 * sin(u_time * 3.0 * u_p1));
    float r = texture(u_tex, v_uv + vec2(o, 0.0)).r;
    float g = texture(u_tex, v_uv).g;
    float b = texture(u_tex, v_uv - vec2(o, 0.0)).b;
    vec3 col = vec3(r, g, b);
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
