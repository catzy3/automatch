#version 330

// CCTV multicam grid. Splits the frame into a configurable rows × cols grid.
// Each cell can independently:
//   - show live, or a memory frame (0.2s / 1s / 3s back)
//   - be inverted to negative
//   - be tinted by a per-cell hash color for a "different camera" feel
//   - have a desaturate filter
//
// Memory selection is packed into u_delay_mask: 2 bits per cell (00=live,
// 01=short, 10=mid, 11=long). u_invert_mask is 1 bit per cell. Up to 16 cells
// (4x4), bits enumerated row-major from top-left.

uniform sampler2D u_tex;        // current
uniform sampler2D u_mem_short;
uniform sampler2D u_mem_mid;
uniform sampler2D u_mem_long;
uniform float u_cols;            // 1..4
uniform float u_rows;            // 1..4
uniform float u_gap;             // 0..0.05
uniform float u_invert_mask;     // bitmask up to 16 cells
uniform float u_delay_mask;      // 2 bits per cell, packed as float (up to ~32 bits ok)
uniform float u_tint_strength;   // per-cell color tint
uniform float u_desat;           // per-cell desaturation (alternates)
uniform float u_label_brightness;// timestamp/cell label brightness in corner
uniform float u_time;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

int getBit(float mask, int bit) {
    float m = mask;
    float power = pow(2.0, float(bit));
    return int(mod(floor(m / power), 2.0));
}
int get2Bit(float mask, int slot) {
    float m = mask;
    float power = pow(2.0, float(slot * 2));
    return int(mod(floor(m / power), 4.0));
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float cols = floor(clamp(u_cols, 1.0, 4.0));
    float rows = floor(clamp(u_rows, 1.0, 4.0));
    vec2 cell = vec2(1.0 / cols, 1.0 / rows);
    vec2 q = floor(v_uv / cell);
    int qi = int(q.x + q.y * cols);
    vec2 local = (v_uv - q * cell) / cell;

    // gap = thin black borders
    vec2 e = abs(local - 0.5);
    float gapMask = step(0.5 - u_gap, max(e.x, e.y));

    // pick source for this cell
    int delay = get2Bit(u_delay_mask, qi);
    vec3 src;
    if (delay == 0)      src = texture(u_tex,       local).rgb;
    else if (delay == 1) src = texture(u_mem_short, local).rgb;
    else if (delay == 2) src = texture(u_mem_mid,   local).rgb;
    else                 src = texture(u_mem_long,  local).rgb;

    // invert?
    if (getBit(u_invert_mask, qi) == 1) src = 1.0 - src;

    // per-cell color tint from hash, very subtle
    vec3 tint = vec3(
        0.85 + 0.30 * hash11(float(qi) * 1.7),
        0.85 + 0.30 * hash11(float(qi) * 2.7),
        0.85 + 0.30 * hash11(float(qi) * 3.7)
    );
    src = mix(src, src * tint, u_tint_strength);

    // alternating desaturation: every other cell
    float gray = dot(src, vec3(0.299, 0.587, 0.114));
    float desat = u_desat * float(int(mod(float(qi), 2.0)));
    src = mix(src, vec3(gray), desat);

    // corner brackets in each cell (top-left + bottom-right)
    float bracket = 0.0;
    float bsize = 0.06;
    float bthick = 0.008;
    // top-left
    if (local.x < bsize && local.y < bthick) bracket = 1.0;
    if (local.y < bsize && local.x < bthick) bracket = 1.0;
    // bottom-right
    if (local.x > 1.0 - bsize && local.y > 1.0 - bthick) bracket = 1.0;
    if (local.y > 1.0 - bsize && local.x > 1.0 - bthick) bracket = 1.0;

    vec3 wet = mix(src, vec3(0.0), gapMask);
    wet += vec3(bracket * u_label_brightness * 0.6);

    // tiny per-cell "REC" dot in top-right of each cell, blinking
    vec2 dotPos = vec2(0.93, 0.93);
    float dotDist = length(local - dotPos);
    float blink = step(0.5, fract(u_time * 0.7 + float(qi) * 0.13));
    if (dotDist < 0.015) wet = mix(wet, vec3(1.0, 0.1, 0.1), blink * u_label_brightness);

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
