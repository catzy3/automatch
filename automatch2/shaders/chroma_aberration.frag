#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // dispersion strength

// Radial lens dispersion — channels spread toward the edges.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 c = v_uv - 0.5;
    float d = dot(c, c);
    vec2 dir = c * d * 0.9 * u_p0;
    float r = texture(u_tex, clamp(v_uv - dir, 0.0, 1.0)).r;
    float g = texture(u_tex, v_uv).g;
    float b = texture(u_tex, clamp(v_uv + dir, 0.0, 1.0)).b;
    vec3 col = vec3(r, g, b);
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
