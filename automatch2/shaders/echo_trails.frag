#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;   // current frame
uniform sampler2D u_prev;  // previous chain output (feedback)
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// Echo / onion-skin trails: motion leaves a fading ghost.
void main() {
    vec3 cur = texture(u_tex, v_uv).rgb;
    vec3 prev = texture(u_prev, v_uv).rgb;
    vec3 col = max(cur, prev * 0.92);
    fragColor = vec4(mix(cur, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
