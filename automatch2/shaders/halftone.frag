#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // dot size

// Halftone dot screen: dot size tracks luminance, dots keep the source colour.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    float aspect = u_resolution.x / max(u_resolution.y, 1.0);
    float scale = 90.0 / max(u_p0, 0.1);
    vec2 p = v_uv * vec2(aspect, 1.0) * scale;
    vec2 cell = fract(p) - 0.5;
    float lum = dot(src, vec3(0.299, 0.587, 0.114));
    float radius = sqrt(max(0.0, 1.0 - lum)) * 0.5; // dark → bigger dot
    float d = length(cell);
    float ink = smoothstep(radius, radius - 0.06, d);
    vec3 col = src * ink; // coloured dots on black
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
