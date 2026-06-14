#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// N-fold radial mirror kaleidoscope with slow rotation.
const float N = 6.0;
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    float aspect = u_resolution.x / max(u_resolution.y, 1.0);
    vec2 c = v_uv - 0.5;
    c.x *= aspect;
    float r = length(c);
    float a = atan(c.y, c.x) + u_time * 0.2;
    float seg = 6.2831853 / N;
    a = mod(a, seg);
    a = abs(a - seg * 0.5);
    vec2 uv = vec2(cos(a), sin(a)) * r;
    uv.x /= aspect;
    uv += 0.5;
    vec3 col = texture(u_tex, clamp(uv, 0.0, 1.0)).rgb;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
