#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// CRT scanlines + vignette + faint rolling bright bar.
void main() {
    vec3 c = texture(u_tex, v_uv).rgb;
    float scan = 0.82 + 0.18 * sin(v_uv.y * u_resolution.y * 3.14159);
    float vig = smoothstep(1.25, 0.35, length(v_uv - 0.5) * 1.6);
    float roll = 0.04 * sin(v_uv.y * 6.2831 - u_time * 2.0);
    vec3 col = c * scan * vig + roll;
    fragColor = vec4(mix(c, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
