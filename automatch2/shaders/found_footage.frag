#version 330 core
// Ported faithfully from v1: "cursed videotape" degradation — dropout bands,
// channel loss, scan-line warp, ghost echo, luma crush, chroma bleed.
uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;

uniform float u_dropout_density;
uniform float u_dropout_strength;
uniform float u_warp_amount;
uniform float u_ghost_opacity;
uniform float u_chroma_bleed;
uniform float u_luma_crush;

in  vec2 v_uv;
out vec4 out_color;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec2 uv = v_uv;
    vec2 pix = uv * u_resolution;

    float warpClock = floor(u_time * 12.0);
    float lineY     = floor(pix.y);
    float drift = sin(lineY * 0.07 + u_time * 1.3) * 0.4;
    float kick  = (hash11(lineY + warpClock * 7.7) - 0.5) * 2.0
                  * step(0.93, hash11(lineY * 3.1 + warpClock));
    float warp = (drift + kick) * u_warp_amount;
    uv.x = clamp(uv.x + warp, 0.0, 1.0);

    float bleed = max(0.0, u_chroma_bleed) / u_resolution.x;
    vec3 col;
    if (bleed > 0.0001) {
        float r = texture(u_tex, uv + vec2(bleed * 2.0, 0.0)).r;
        float g = texture(u_tex, uv + vec2(bleed,       0.0)).g;
        float b = texture(u_tex, uv).b;
        col = vec3(r, g, b);
    } else {
        col = texture(u_tex, uv).rgb;
    }

    if (u_ghost_opacity > 0.001) {
        float ghostShift = 0.003 + sin(u_time * 0.4) * 0.001;
        vec3  ghost = texture(u_tex, uv + vec2(ghostShift, ghostShift * 0.5)).rgb;
        float ghostLuma = dot(ghost, vec3(0.299, 0.587, 0.114));
        ghost = mix(ghost, vec3(ghostLuma), 0.6);
        col = mix(col, ghost, u_ghost_opacity);
    }

    float dropClock = floor(u_time * 6.0);
    float bandSeed  = hash21(vec2(lineY * 0.02, dropClock));
    float inBand    = step(1.0 - u_dropout_density * 0.35, bandSeed);
    if (inBand > 0.5) {
        float luma = dot(col, vec3(0.299, 0.587, 0.114));
        float noise = hash21(vec2(pix.x * 0.13 + dropClock, lineY)) * 0.25;
        vec3 damaged = vec3(luma + noise) * (1.0 - u_dropout_strength * 0.7);
        float killSeed = hash21(vec2(dropClock + 3.7, lineY * 0.01));
        if (killSeed < 0.33)      damaged.r = damaged.r * (1.0 - u_dropout_strength);
        else if (killSeed < 0.66) damaged.g = damaged.g * (1.0 - u_dropout_strength);
        else                      damaged.b = damaged.b * (1.0 - u_dropout_strength);
        col = mix(col, damaged, u_dropout_strength);
    }

    float blockSize = 24.0;
    vec2  blockIdx  = floor(pix / blockSize);
    float blockSeed = hash21(blockIdx + floor(u_time * 4.0) * 0.13);
    if (blockSeed > 1.0 - u_dropout_density * 0.2) {
        float ch = hash21(blockIdx * 7.3 + 2.1);
        if      (ch < 0.33) col.rg = vec2(0.0);
        else if (ch < 0.66) col.gb = vec2(0.0);
        else                col.rb = vec2(0.0);
    }

    if (u_luma_crush > 0.001) {
        float crushWave = sin(u_time * 2.1 + uv.y * 3.0) * 0.5 + 0.5;
        float crushMask = smoothstep(0.85, 1.0, crushWave);
        col = mix(col, col * (1.0 - crushMask * 0.6), u_luma_crush);
    }

    vec3 dry = texture(u_tex, v_uv).rgb;
    out_color = vec4(mix(dry, col, u_mix), 1.0);
}
