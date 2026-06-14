#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// Deterministic invert — used only by the effect-chain selftest (not a
// featured effect). out = 1 - src, which is trivial to verify via readback.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec3 col = vec3(1.0) - src;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
