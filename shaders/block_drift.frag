#version 330

// Block drift with size jitter, neighbour swaps and chaos:
//   u_block_size     — base block size (0..1 → 24..160 px)
//   u_drift_amount   — drift velocity
//   u_refresh_rate   — how often a block grabs a fresh sample
//   u_decay          — content aging
//   u_chaos          — block-size variance (some blocks bigger/smaller)
//   u_swap_rate      — chance a block trades content with a neighbour
//   u_warp           — extra warp inside drifting blocks (twist)
//   u_mix            — wet/dry

uniform sampler2D u_tex;
uniform sampler2D u_prev;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_block_size;
uniform float u_drift_amount;
uniform float u_refresh_rate;
uniform float u_decay;
uniform float u_chaos;
uniform float u_swap_rate;
uniform float u_warp;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // base block partition (jittered per "super-cell" so block boundaries vary)
    vec2 superCell = floor(v_uv * u_resolution / 240.0);
    float sizeJit = mix(1.0, 0.4 + 1.5 * hash21(superCell), u_chaos);
    float bsize = mix(24.0, 160.0, clamp(u_block_size, 0.0, 1.0)) * sizeJit;
    vec2 block = floor(v_uv * u_resolution / bsize);

    // drift vector for this block
    float angle = hash21(block) * 6.28318 + u_time * 0.05 * hash21(block + 5.0);
    vec2 dir = vec2(cos(angle), sin(angle));
    float speed = 0.0006 * u_drift_amount * (0.25 + hash21(block + 1.7));

    // optional rotational warp inside the block (twist about its center)
    vec2 blockCenter = (block + 0.5) * bsize / u_resolution;
    vec2 toCenter = v_uv - blockCenter;
    if (u_warp > 0.001) {
        float twist = (hash21(block + 9.0) - 0.5) * u_warp * 0.8;
        float ca = cos(twist); float sa = sin(twist);
        toCenter = vec2(toCenter.x*ca - toCenter.y*sa,
                        toCenter.x*sa + toCenter.y*ca);
    }
    vec2 sampUV = blockCenter + toCenter;

    // sample prev, drifted
    vec3 prev = texture(u_prev, sampUV - dir * speed).rgb;
    prev *= u_decay;

    // neighbour swap: a block may grab from one of its 8 neighbours
    if (u_swap_rate > 0.001) {
        float swapRoll = hash21(block + floor(u_time * 1.7));
        if (swapRoll < u_swap_rate) {
            float a2 = hash21(block + 17.0) * 6.28318;
            vec2 nbrOffset = round(vec2(cos(a2), sin(a2))) * (bsize / u_resolution);
            vec3 nbr = texture(u_prev, sampUV - dir * speed + nbrOffset).rgb;
            prev = mix(prev, nbr, 0.7);
        }
    }

    // refresh tick — block grabs a fresh sample on its own clock
    float tick = floor(u_time * (0.4 + 2.0 * u_refresh_rate));
    float refreshRoll = hash21(block + tick * 13.7);
    float refreshThresh = 0.05 + 0.35 * u_refresh_rate;
    float refresh = step(1.0 - refreshThresh, refreshRoll);

    float tickPhase = fract(u_time * (0.4 + 2.0 * u_refresh_rate));
    float edge = smoothstep(0.0, 0.08, tickPhase) * (1.0 - smoothstep(0.92, 1.0, tickPhase));

    vec3 drifted = mix(prev, dry, refresh * mix(0.7, 1.0, edge));
    fragColor = vec4(mix(dry, drifted, u_mix), 1.0);
}
