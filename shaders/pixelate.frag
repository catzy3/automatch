#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  pixelate — blocky pixelation fed through a RECURSIVE FEEDBACK LOOP
//
//  TouchDesigner "feedback-on-pixelate" / pixel-memory-decay look:
//    1. The live frame is crushed to chunky NEAREST blocks (u_block px).
//    2. The effect's OWN previous output (u_prev) is sampled back each frame,
//       slightly zoomed about centre and drifted, then decayed (u_feedback).
//    3. New pixel frame + decayed history are combined (lighten / add / over).
//  Because the history is re-zoomed + re-decayed every frame, the blocks
//  smear, crawl outward and slowly dissolve → evolving "pixel memory decay".
//
//  Bound like motion_trails / slit_scan:
//    u_current = slot 0 (live frame entering this effect)
//    u_prev    = slot 1 (this effect's output from the previous frame)
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_current;
uniform sampler2D u_prev;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;
uniform float u_blend;      // final composite mode (see _blendOp)
uniform float u_block;      // pixel block size in px (bigger = chunkier)
uniform float u_feedback;   // 0..1 history persistence / trail length
uniform float u_zoom;       // 0..1 → feedback scale 0.96..1.04 (centre zoom)
uniform float u_drift;      // 0..1 → feedback vertical drift
uniform float u_fb_mode;    // 0 lighten(max)  1 add  2 over(mix)

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
    vec3 dry = texture(u_current, v_uv).rgb;

    // ── 1) pixelate the live frame: snap UV to block-centre, NEAREST look ──
    vec2 grid = max(u_block, 1.0) / u_resolution;
    vec2 puv  = (floor(v_uv / grid) + 0.5) * grid;
    vec3 px   = texture(u_current, puv).rgb;

    // ── 2) recursive feedback: zoom about centre + drift + decay ──
    float z   = 1.0 + (u_zoom * 2.0 - 1.0) * 0.04;     // 0.96 .. 1.04
    vec2  fuv = (v_uv - 0.5) / max(z, 1e-3) + 0.5;
    fuv.y    += u_drift * 0.012;                        // gentle upward crawl
    float fb  = clamp(u_feedback, 0.0, 1.0);
    vec3  hist = texture(u_prev, fuv).rgb * fb;

    // ── 3) combine new pixels with decayed history ──
    int  fm = int(u_fb_mode + 0.5);
    vec3 wet;
    if      (fm == 1) wet = min(px + hist, 1.0);                 // add
    else if (fm == 2) wet = mix(px, hist, fb * 0.6);             // over
    else              wet = max(px, hist);                       // lighten

    fragColor = vec4(composite(dry, clamp(wet, 0.0, 1.0), u_mix, u_blend), 1.0);
}
