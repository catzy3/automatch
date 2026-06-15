#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // intensity

float hash1(float p) { return fract(sin(p * 78.233) * 43758.5453); }
float hash2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

// VHS: per-line horizontal jitter, chroma bleed, scanlines, tape noise.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 uv = v_uv;
    float line = floor(v_uv.y * 220.0);
    float jitter = (hash1(line + floor(u_time * 15.0)) * 2.0 - 1.0);
    uv.x += jitter * 0.0035 * u_p0;

    float o = 3.0 * u_p0 / max(u_resolution.x, 1.0);
    vec3 col;
    col.r = texture(u_tex, clamp(uv + vec2(o, 0.0), 0.0, 1.0)).r;
    col.g = texture(u_tex, uv).g;
    col.b = texture(u_tex, clamp(uv - vec2(o, 0.0), 0.0, 1.0)).b;

    col *= 0.88 + 0.12 * sin(v_uv.y * u_resolution.y * 1.5);   // scanlines
    float n = hash2(v_uv * vec2(133.0, 71.0) + u_time);
    col += (n - 0.5) * 0.09 * u_p0;                            // tape noise

    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
