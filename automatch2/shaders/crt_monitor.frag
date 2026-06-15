#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  crt_monitor — second CRT variant (barrel + aperture grille + bloom)
//
//  Distinct from the existing `crt` effect: this one leans on barrel screen
//  curvature, an RGB aperture-grille mask, corner vignette, soft phosphor
//  bloom and edge chromatic aberration — a chunkier "consumer TV" look rather
//  than the crisp scanline monitor of the original.
//
//  Chromatic aberration modes (u_aberration_mode):
//    0 — radial RGB split: R pushed outward, B inward, G unchanged
//        magnitude = u_aberration * distance²
//    1 — per-channel barrel distortion: each channel gets a slightly different
//        barrel curve, producing a lens-fringe look that compounds with u_curvature
//    2 — scanline-phase shift: R/G/B sampling offsets oscillate with screen-Y,
//        simulating RF carrier crosstalk / chroma misalignment on a composite signal
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_mix;
uniform float u_curvature;       // barrel amount (0..1)
uniform float u_scanline;        // scanline darkening (0..1)
uniform float u_grille;          // aperture-grille mask strength (0..1)
uniform float u_vignette;        // corner darkening (0..1)
uniform float u_bloom;           // phosphor bloom (0..1)
uniform float u_aberration;      // edge RGB split magnitude (0..1)
uniform float u_aberration_mode; // 0=radial split  1=barrel+split  2=scanline-phase
uniform float u_blend;           // composite mode (see _blendOp): 0=cross 1=add…

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

vec2 barrel(vec2 uv, float amt) {
    vec2 c = uv * 2.0 - 1.0;
    float r2 = dot(c, c);
    c *= 1.0 + amt * 0.30 * r2;
    return c * 0.5 + 0.5;
}

// Mode 1 helper — barrel with an independent per-channel curvature tweak.
// channelBias: positive = more barrel (R), negative = less (B).
vec2 barrelChannel(vec2 uv, float baseCurve, float channelBias) {
    vec2 c = uv * 2.0 - 1.0;
    float r2 = dot(c, c);
    float amt = baseCurve + channelBias;
    c *= 1.0 + amt * 0.30 * r2;
    return c * 0.5 + 0.5;
}

vec3 sampleBloom(vec2 uv) {
    vec2 px = 1.0 / u_resolution;
    vec3 s = texture(u_tex, uv).rgb * 0.5;
    s += texture(u_tex, uv + vec2( px.x, 0.0) * 2.0).rgb * 0.125;
    s += texture(u_tex, uv + vec2(-px.x, 0.0) * 2.0).rgb * 0.125;
    s += texture(u_tex, uv + vec2(0.0,  px.y) * 2.0).rgb * 0.125;
    s += texture(u_tex, uv + vec2(0.0, -px.y) * 2.0).rgb * 0.125;
    return s;
}

// ─────────────────────────────────────────────────────────────────────────────
//  aberrationSample — dispatches to the correct mode and returns RGB.
//  uv         : post-barrel UV (already clipped to [0,1])
//  baseCurve  : u_curvature, forwarded to mode 1
//  magnitude  : u_aberration (0..1)
//  mode       : 0 / 1 / 2
// ─────────────────────────────────────────────────────────────────────────────
vec3 aberrationSample(vec2 uv, float baseCurve, float magnitude, int mode) {

    // ── Mode 0: radial RGB split ──────────────────────────────────────────
    // Canonical CRT edge fringe: R offset outward from centre, B inward, G clean.
    // offset vector = centred NDC coord scaled by magnitude * r²
    if (mode == 0) {
        vec2 c    = uv * 2.0 - 1.0;
        float r2  = dot(c, c);
        vec2  ab  = c * (magnitude * 0.01 * r2);
        return vec3(
            texture(u_tex, uv + ab).r,
            texture(u_tex, uv     ).g,
            texture(u_tex, uv - ab).b
        );
    }

    // ── Mode 1: per-channel barrel distortion + radial split ──────────────
    // Each colour channel is warped by a slightly different barrel coefficient.
    // R gets the most curvature (pulls outward), B the least (appears inward).
    // The difference in barrel remapping naturally amplifies toward corners,
    // compounding with the base u_curvature lens already applied to uv.
    // channelBias magnitudes are proportional to u_aberration.
    if (mode == 1) {
        // Scale factor chosen so that magnitude=1 gives ~0.15 extra barrel —
        // visually significant but not so large that edges wrap past 0/1.
        float bias = magnitude * 0.15;
        vec2 uvR = barrelChannel(uv, baseCurve,  bias);          // most curved
        vec2 uvG = barrelChannel(uv, baseCurve,  0.0);           // neutral
        vec2 uvB = barrelChannel(uv, baseCurve, -bias);          // least curved

        // Clamp channels that barrel off-screen to the border pixel rather than
        // wrapping — preserves the CRT black-border look.
        uvR = clamp(uvR, 0.0, 1.0);
        uvG = clamp(uvG, 0.0, 1.0);
        uvB = clamp(uvB, 0.0, 1.0);

        // Additional radial split layered on top (half strength) keeps fringe
        // visible even near the centre where barrel deltas are tiny.
        vec2 cent = uv * 2.0 - 1.0;
        float r2  = dot(cent, cent);
        vec2  ab  = cent * (magnitude * 0.005 * r2);

        return vec3(
            texture(u_tex, uvR + ab).r,
            texture(u_tex, uvG     ).g,
            texture(u_tex, uvB - ab).b
        );
    }

    // ── Mode 2: scanline-phase shift per channel ──────────────────────────
    // Simulates RF/composite chroma crosstalk: each colour channel's horizontal
    // sample position oscillates with screen-Y at a slightly different spatial
    // frequency, as if R, G and B were sub-carriers beating against each other.
    // The vertical component adds a small phase-dependent Y jitter.
    //
    // freqR/G/B: cycles per screen height — chosen as nearby integers so the
    //   three waveforms slowly drift in and out of phase, creating the banding
    //   pattern characteristic of cheap composite decoders.
    // magnitude * 0.004 keeps the peak horizontal shift under ~4 px at HD res.
    if (mode == 2) {
        const float PI2     = 6.28318530718;
        const float freqR   = 53.0;   // cycles / screen height (R carrier)
        const float freqG   = 57.0;   // slightly higher (G carrier)
        const float freqB   = 61.0;   // even higher (B carrier)
        const float yScale  = 0.4;    // vertical jitter fraction of horizontal

        float y = uv.y * u_resolution.y;     // pixel row (continuous)
        float px = 1.0 / u_resolution.x;     // one texel in U

        // Per-channel horizontal phase offset in UV space
        float shiftR = sin(y * PI2 / u_resolution.y * freqR) * magnitude * 0.004;
        float shiftG = sin(y * PI2 / u_resolution.y * freqG) * magnitude * 0.004;
        float shiftB = sin(y * PI2 / u_resolution.y * freqB) * magnitude * 0.004;

        // Small vertical jitter derived from the same oscillator (quadrature)
        float jitR = cos(y * PI2 / u_resolution.y * freqR) * magnitude * 0.002 * yScale;
        float jitG = cos(y * PI2 / u_resolution.y * freqG) * magnitude * 0.002 * yScale;
        float jitB = cos(y * PI2 / u_resolution.y * freqB) * magnitude * 0.002 * yScale;

        vec2 uvR = clamp(uv + vec2(shiftR, jitR), 0.0, 1.0);
        vec2 uvG = clamp(uv + vec2(shiftG, jitG), 0.0, 1.0);
        vec2 uvB = clamp(uv + vec2(shiftB, jitB), 0.0, 1.0);

        return vec3(
            texture(u_tex, uvR).r,
            texture(u_tex, uvG).g,
            texture(u_tex, uvB).b
        );
    }

    // Fallback: passthrough (unknown mode)
    return texture(u_tex, uv).rgb;
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    vec2 uv = barrel(v_uv, u_curvature);
    // off-screen after curvature → black border
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        fragColor = vec4(mix(dry, vec3(0.0), u_mix), 1.0);
        return;
    }

    // chromatic aberration — mode dispatched to aberrationSample()
    vec3 col = aberrationSample(uv, u_curvature, u_aberration, int(u_aberration_mode + 0.5));

    // phosphor bloom
    col = mix(col, max(col, sampleBloom(uv)), u_bloom);

    // aperture grille — repeating R/G/B vertical triads
    float cx = mod(floor(uv.x * u_resolution.x), 3.0);
    vec3 mask = vec3(cx == 0.0 ? 1.0 : 0.6,
                     cx == 1.0 ? 1.0 : 0.6,
                     cx == 2.0 ? 1.0 : 0.6);
    col *= mix(vec3(1.0), mask, u_grille);

    // scanlines
    float scan = 1.0 - u_scanline * 0.5 * (0.5 + 0.5 * sin(uv.y * u_resolution.y * 3.14159));
    col *= scan;

    // vignette
    vec2 cent = uv * 2.0 - 1.0;
    float edge = dot(cent, cent);
    float vig = 1.0 - u_vignette * smoothstep(0.5, 1.6, edge);
    col *= vig;

    fragColor = vec4(composite(dry, col, u_mix, u_blend), 1.0);
}
