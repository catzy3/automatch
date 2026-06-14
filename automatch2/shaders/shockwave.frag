#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// Radial ripple emanating from the centre — refraction shockwave.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 c = v_uv - 0.5;
    // correct for aspect so rings stay circular
    c.x *= u_resolution.x / max(u_resolution.y, 1.0);
    float d = length(c);
    float wave = sin(d * 45.0 - u_time * 6.0) * exp(-d * 3.5) * 0.02;
    vec2 dir = c / (d + 1e-5);
    dir.x /= u_resolution.x / max(u_resolution.y, 1.0);
    vec2 uv = clamp(v_uv + dir * wave, 0.0, 1.0);
    vec3 col = texture(u_tex, uv).rgb;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
