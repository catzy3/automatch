#version 330

// 4-up surveillance grid. Each quadrant can independently invert, scale,
// or freeze (when paired with motion_trails the bottom-right becomes a
// long-exposure trail like in the CCTV reference).

uniform sampler2D u_tex;
uniform sampler2D u_prev;     // for the freeze/trail quadrant
uniform float u_gap;          // 0..0.05 — black bezel between quads
uniform float u_invert_mask;  // packed 4 bits as float: which quads invert
uniform float u_freeze_q;     // 0..1 — strength of freeze on quad 3
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // determine which quadrant we're in
    vec2 q = step(vec2(0.5), v_uv);
    int qi = int(q.x) + 2 * int(q.y);      // 0:bl, 1:br, 2:tl, 3:tr

    // local uv inside the quadrant, remapped to full 0..1
    vec2 local = (v_uv - q * 0.5) * 2.0;

    // gap mask: paint thin black borders
    vec2 e = abs(local - 0.5);
    float gapMask = step(0.5 - u_gap, max(e.x, e.y));

    vec3 src = texture(u_tex, local).rgb;
    if (qi == 3) {
        // freeze / trail quad: blend previous output too
        vec3 p = texture(u_prev, local).rgb;
        src = mix(src, p, u_freeze_q);
    }

    // invert this quadrant?
    int mask = int(u_invert_mask);
    if (((mask >> qi) & 1) == 1) src = 1.0 - src;

    vec3 wet = mix(src, vec3(0.0), gapMask);

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
