#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// Sine domain-warp — liquid wobble.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 uv = v_uv;
    uv.x += 0.012 * sin(uv.y * 22.0 + u_time * 3.0);
    uv.y += 0.012 * sin(uv.x * 18.0 + u_time * 2.3);
    uv = clamp(uv, 0.0, 1.0);
    vec3 col = texture(u_tex, uv).rgb;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
