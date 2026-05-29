#version 330

// Light-bleed accumulator with extra randomness:
//   u_threshold  — luma threshold for bright-pixel extraction
//   u_decay      — old-bloom decay per frame
//   u_spread     — diffusion radius
//   u_warmth     — warm color tint on bloom
//   u_chaos      — random hot-spot probability (sparkle)
//   u_dance      — periodic per-pixel offset wobble (bloom "breathes")
//   u_floor      — minimum brightness floor that always seeds bloom

uniform sampler2D u_tex;
uniform sampler2D u_prev;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_threshold;
uniform float u_decay;
uniform float u_spread;
uniform float u_warmth;
uniform float u_chaos;
uniform float u_dance;
uniform float u_floor;

in vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec2 px = 1.0 / u_resolution;
    float r = 1.0 + 6.0 * u_spread;

    // "dance": low-freq wobble in the sample position so the bloom isn't static
    vec2 wobble = vec2(
        sin(u_time * 0.7 + v_uv.y * 12.0),
        cos(u_time * 0.9 + v_uv.x * 9.0)
    ) * px * 1.5 * u_dance;

    vec3 prev = vec3(0.0);
    float wsum = 0.0;
    for (int j = -1; j <= 1; ++j) {
        for (int i = -1; i <= 1; ++i) {
            float w = 1.0 - 0.3 * length(vec2(i, j));
            prev += texture(u_prev, v_uv + wobble + vec2(float(i), float(j)) * px * r).rgb * w;
            wsum += w;
        }
    }
    prev /= wsum;
    prev *= u_decay;

    vec3 cur = texture(u_tex, v_uv).rgb;
    float luma = dot(cur, vec3(0.299, 0.587, 0.114));

    // basic threshold extraction
    float bright = max(0.0, luma - u_threshold) / max(1.0 - u_threshold, 1e-4);
    bright = pow(bright, 1.5);

    // floor: even mid-bright regions seed a little bloom
    bright = max(bright, u_floor * luma);

    // chaos: a small chance per pixel that this pixel is a "spike" (extra bloom)
    float spikeRoll = hash21(floor(v_uv * 480.0) + floor(u_time * 12.0));
    float spike = step(1.0 - u_chaos * 0.05, spikeRoll) * 2.0;
    bright += spike;

    vec3 add = cur * bright * 0.9;
    vec3 warmTint = vec3(1.15, 0.92, 0.65);
    add = mix(add, add * warmTint, u_warmth);

    fragColor = vec4(prev + add, 1.0);
}
