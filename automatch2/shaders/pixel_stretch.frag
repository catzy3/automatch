#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // drift speed

// Pixel-stretch / smear: a drifting seed column is dragged across the frame —
// the classic datamosh "pulled pixels" streak.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    float seed = fract(u_time * 0.13 * u_p0);    // 0..1 seed column, drifts
    vec3 col;
    if (v_uv.x > seed) {
        // stretch the seed column rightward, with a little vertical wobble
        float y = v_uv.y + 0.01 * sin(v_uv.x * 30.0 + u_time);
        col = texture(u_tex, vec2(seed, clamp(y, 0.0, 1.0))).rgb;
    } else {
        col = src;
    }
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
