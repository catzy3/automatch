#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

float lum(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

// Sobel edge detection → glowing neon contours with a cycling hue.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 px = 1.0 / max(u_resolution, vec2(1.0));
    float tl = lum(texture(u_tex, v_uv + vec2(-px.x, px.y)).rgb);
    float tm = lum(texture(u_tex, v_uv + vec2(0.0, px.y)).rgb);
    float tr = lum(texture(u_tex, v_uv + vec2(px.x, px.y)).rgb);
    float ml = lum(texture(u_tex, v_uv + vec2(-px.x, 0.0)).rgb);
    float mr = lum(texture(u_tex, v_uv + vec2(px.x, 0.0)).rgb);
    float bl = lum(texture(u_tex, v_uv + vec2(-px.x, -px.y)).rgb);
    float bm = lum(texture(u_tex, v_uv + vec2(0.0, -px.y)).rgb);
    float br = lum(texture(u_tex, v_uv + vec2(px.x, -px.y)).rgb);
    float gx = (tr + 2.0 * mr + br) - (tl + 2.0 * ml + bl);
    float gy = (tl + 2.0 * tm + tr) - (bl + 2.0 * bm + br);
    float e = clamp(sqrt(gx * gx + gy * gy) * 2.2, 0.0, 1.0);
    vec3 neon = 0.5 + 0.5 * cos(u_time * 2.0 + vec3(0.0, 2.094, 4.188) + e * 3.0);
    vec3 col = neon * e;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
