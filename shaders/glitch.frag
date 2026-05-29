#version 330

uniform sampler2D u_tex;
uniform sampler2D u_prev;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_block_size;
uniform float u_drop_chance;
uniform float u_repeat_chance;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec2 uv = v_uv;

    // horizontal sync errors
    float row = uv.y * u_resolution.y;
    float syncT = floor(u_time * 5.0);
    if (hash11(syncT) < u_drop_chance * 0.4) {
        float syncCenter = hash11(syncT * 1.7);
        float syncHalf = 0.005 + 0.025 * u_drop_chance;
        float inSync = smoothstep(syncHalf, 0.0, abs(uv.y - syncCenter));
        float tearAmt = (hash11(syncT * 2.1) - 0.5) * 0.3;
        uv.x = fract(uv.x + tearAmt * inSync);
    }

    // macroblock displacement
    float bsize = mix(16.0, 96.0, clamp(u_block_size, 0.0, 1.0));
    vec2 block = floor(v_uv * u_resolution / bsize);
    float t = floor(u_time * 6.0);
    float roll = hash21(block + t);

    if (roll < u_drop_chance) {
        float shift = (hash21(block + t * 1.7) - 0.5) * 0.15;
        uv.x = fract(uv.x + shift);
    }

    vec3 wet;

    // stuck block from prev frame
    float repeatRoll = hash21(block * 1.3 + floor(t * 0.5));
    if (repeatRoll < u_repeat_chance) {
        float ps = (hash21(block + t * 0.3) - 0.5) * 0.1;
        vec2 puv = vec2(fract(v_uv.x + ps), v_uv.y);
        wet = texture(u_prev, puv).rgb;
    }
    // full-frame freeze (rare)
    else if (hash11(t * 0.41) < u_drop_chance * 0.04) {
        wet = texture(u_prev, v_uv).rgb;
    }
    else {
        wet = texture(u_tex, uv).rgb;
    }

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
