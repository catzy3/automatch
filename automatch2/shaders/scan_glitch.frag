#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

float hash1(float p) { return fract(sin(p * 78.233) * 43758.5453); }

// Signal tear: random horizontal bands shift sideways with channel desync.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    float band = floor(v_uv.y * 26.0);
    float n = hash1(band + floor(u_time * 8.0));
    float shift = n > 0.82 ? (n - 0.82) * 0.7 : 0.0; // only some bands tear
    vec2 uv = vec2(fract(v_uv.x + shift), v_uv.y);
    vec3 col = texture(u_tex, uv).rgb;
    if (shift > 0.0) {
        // RGB desync on torn bands
        col.r = texture(u_tex, vec2(fract(uv.x + 0.02), uv.y)).r;
        col.b = texture(u_tex, vec2(fract(uv.x - 0.02), uv.y)).b;
    }
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
