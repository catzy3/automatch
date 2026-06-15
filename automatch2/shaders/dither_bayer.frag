#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0;   // colour levels knob (1.0 -> ~4 levels/channel)
uniform float u_p1;   // pixel size knob   (1.0 -> ~4px blocks)

// 4x4 ordered Bayer matrix, values normalised to (0..1) - 0.5
float bayer4(ivec2 p){
    int x = p.x & 3;
    int y = p.y & 3;
    int M[16] = int[16]( 0, 8, 2,10,
                        12, 4,14, 6,
                         3,11, 1, 9,
                        15, 7,13, 5);
    return (float(M[y*4 + x]) + 0.5) / 16.0 - 0.5;
}

void main(){
    vec3 src = texture(u_tex, v_uv).rgb;

    // pixelation block size in screen pixels (2..14)
    float px = floor(mix(2.0, 14.0, clamp(u_p1, 0.0, 1.0)) + 0.5);
    vec2 fragPx = v_uv * u_resolution;
    vec2 block  = floor(fragPx / px);
    vec2 snapUv = (block * px + px * 0.5) / u_resolution;

    vec3 col = texture(u_tex, snapUv).rgb;

    // quantisation levels per channel (2..10), low = chunkier
    float levels = floor(mix(2.0, 10.0, clamp(u_p0, 0.0, 1.0)) + 0.5);

    // animated threshold: slowly drift the Bayer cell so it shimmers
    ivec2 cell = ivec2(block) + ivec2(int(u_time * 3.0), int(u_time * 2.0));
    float t = bayer4(cell);

    // ordered dither: nudge value by threshold scaled to one quant step,
    // then quantise -> classic crunchy 8-bit gradient breakup, hue preserved
    float qstep = 1.0 / (levels - 1.0);
    col += t * qstep;
    col = floor(col * (levels - 1.0) + 0.5) / (levels - 1.0);
    col = clamp(col, 0.0, 1.0);

    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
