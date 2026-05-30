#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  modulate — signal modulation of the image (AM / FM / phase)
//
//  Treats the picture like a broadcast signal carried on a sine wave:
//    mode 0 (AM)    — carrier amplitude multiplies brightness (rolling bars)
//    mode 1 (FM)    — image luma modulates carrier frequency → wavy displacement
//    mode 2 (phase) — luma offsets carrier phase → chroma smear / ringing
//  u_axis picks the carrier orientation; u_speed scrolls it over time.
//  This is purely visual and unrelated to the audio-reactive modulation engine.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_time;
uniform float u_mix;
uniform float u_mode;     // 0 = AM, 1 = FM, 2 = phase
uniform float u_freq;     // carrier frequency (cycles across frame)
uniform float u_amount;   // modulation depth (0..1)
uniform float u_speed;    // carrier scroll speed
uniform float u_axis;     // 0 = vertical bars, 1 = horizontal, between = diagonal
uniform float u_blend;    // composite mode (see _blendOp): 0=cross 1=add 2=screen…

in  vec2 v_uv;
out vec4 fragColor;

// ── TouchDesigner-style Composite TOP: blend `wet` over `dry`, then opacity ──
//   0 cross/over  1 add  2 screen  3 multiply  4 overlay  5 difference
//   6 lighten(max) 7 darken(min) 8 dodge  9 burn  10 subtract
vec3 _blendOp(vec3 b, vec3 s, int m) {
    if (m == 1)  return b + s;
    if (m == 2)  return 1.0 - (1.0 - b) * (1.0 - s);
    if (m == 3)  return b * s;
    if (m == 4)  return mix(2.0 * b * s,
                            1.0 - 2.0 * (1.0 - b) * (1.0 - s),
                            step(0.5, b));
    if (m == 5)  return abs(b - s);
    if (m == 6)  return max(b, s);
    if (m == 7)  return min(b, s);
    if (m == 8)  return b / max(1.0 - s, 1e-4);
    if (m == 9)  return 1.0 - (1.0 - b) / max(s, 1e-4);
    if (m == 10) return b - s;
    return s;                                   // 0 = cross/over → wet only
}
vec3 composite(vec3 dry, vec3 wet, float amount, float blend) {
    vec3 r = clamp(_blendOp(dry, wet, int(blend + 0.5)), 0.0, 1.0);
    return mix(dry, r, clamp(amount, 0.0, 1.0));
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float ang = u_axis * 1.5707963;             // 0..90°
    vec2  dir = vec2(cos(ang), sin(ang));
    float coord = dot(v_uv, dir);
    float twoPi = 6.2831853;
    float carrier = coord * u_freq * twoPi + u_time * u_speed * twoPi;

    int mode = int(u_mode + 0.5);
    vec3 wet;

    if (mode == 0) {
        // amplitude modulation: brightness scaled by carrier
        float am = 1.0 + u_amount * sin(carrier);
        wet = dry * am;
    } else if (mode == 1) {
        // frequency modulation: the carrier sweeps the WHOLE frame in wavy
        // bands (so flat areas visibly slip, not just edges), with image luma
        // bending the carrier for extra wobble.  ~0.18 UV at full depth → bold,
        // readable "signal tearing" like the TouchDesigner FM look.
        float luma = dot(dry, vec3(0.299, 0.587, 0.114));
        float disp = sin(carrier + luma * u_amount * 6.2831) * u_amount * 0.18;
        vec2 perp = vec2(-dir.y, dir.x);
        wet = texture(u_tex, clamp(v_uv + perp * disp, 0.0, 1.0)).rgb;
    } else if (mode == 3) {
        // RGB-carrier bars: each channel rides the carrier 120° apart, so the
        // sine bands become scrolling rainbow vertical stripes.  u_freq = bar
        // count, u_speed scrolls them, u_amount = saturation/strength.  The
        // image luma rides inside the bars so the picture still reads through.
        float luma = dot(dry, vec3(0.299, 0.587, 0.114));
        vec3 bars = vec3(0.5 + 0.5 * sin(carrier),
                         0.5 + 0.5 * sin(carrier + 2.0943951),   // +120°
                         0.5 + 0.5 * sin(carrier + 4.1887902));  // +240°
        vec3 colored = bars * (0.30 + luma);
        wet = mix(dry, colored, u_amount);
    } else {
        // phase modulation: per-channel phase offset → chroma smear/ringing.
        // A shared carrier band displaces the whole frame; luma splits R/B
        // apart for the chromatic fringe.  Strong enough to read across flats.
        float luma = dot(dry, vec3(0.299, 0.587, 0.114));
        float ph   = luma * u_amount * 3.1415;
        vec2 perp  = vec2(-dir.y, dir.x);
        float base = sin(carrier) * u_amount * 0.10;
        float dR = base + sin(carrier + ph) * u_amount * 0.06;
        float dG = base;
        float dB = base + sin(carrier - ph) * u_amount * 0.06;
        wet.r = texture(u_tex, clamp(v_uv + perp * dR, 0.0, 1.0)).r;
        wet.g = texture(u_tex, clamp(v_uv + perp * dG, 0.0, 1.0)).g;
        wet.b = texture(u_tex, clamp(v_uv + perp * dB, 0.0, 1.0)).b;
    }

    fragColor = vec4(composite(dry, clamp(wet, 0.0, 1.0), u_mix, u_blend), 1.0);
}
