#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  vhs — physically-based NTSC composite signal-chain simulation
//
//  Real VHS goes: RGB → YIQ → composite (Y + I·cos(ωt) + Q·sin(ωt))
//                       → bandwidth-limited tape recording
//                       → playback → comb-filter Y/C separator
//                       → YIQ → RGB
//
//  Each stage adds its own physical artifacts.  This shader simulates the
//  meaningful ones:
//    Stage 1 — RGB → YIQ (matrix conversion)
//    Stage 2 — Bandwidth limit: Y to 4.2 MHz, I to 1.3 MHz, Q to 0.5 MHz
//              (lowpass filters of proportional sample radii)
//    Stage 3 — 3.58 MHz chroma subcarrier modulation + phase wobble
//    Stage 4 — Tape transport artifacts:
//              · horizontal jitter (3 layers: drift, kicks, coarse groups)
//              · tracking band (intermittent loss-of-tracking strip)
//              · head-switching dirt at frame bottom (vertical retrace)
//              · tape dropouts (random magnetic patches that fail entirely)
//    Stage 5 — Color killer: when chroma amplitude falls below threshold,
//              the decoder mutes colour completely (real VCR behaviour)
//    Stage 6 — Y/C separator overshoot (high-frequency ringing on edges)
//    Stage 7 — AGC pumping + magenta cast (oxidised tape colour shift)
//
//  Bandwidth limits use horizontal box-filter taps; the asymmetric widths
//  reproduce the smearing-of-chroma look authentically.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_tracking_noise;
uniform float u_line_jitter;
uniform float u_chroma_blur;
uniform float u_color_loss;
uniform float u_head_switch;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

// ───── hashing & noise ─────
float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float noise1d(float x) {
    float i = floor(x); float f = fract(x);
    float a = hash11(i); float b = hash11(i + 1.0);
    float u = f * f * (3.0 - 2.0 * f);
    return mix(a, b, u);
}

// ───── colour space ─────
//  Standard NTSC matrices (FCC). I axis is along orange/cyan, Q axis along
//  green/magenta.  Y bandwidth = 4.2 MHz, I = 1.3 MHz, Q = 0.5 MHz on real
//  broadcast — we map those ratios into pixel-radius lowpasses.
vec3 rgb2yiq(vec3 c) {
    return mat3(0.299, 0.596, 0.211,
                0.587,-0.274,-0.523,
                0.114,-0.322, 0.312) * c;
}
vec3 yiq2rgb(vec3 c) {
    return mat3(1.0, 1.0, 1.0,
                0.956,-0.272,-1.106,
                0.621,-0.647, 1.703) * c;
}

// Box-lowpass on a single channel along horizontal (the scan direction).
// `radius` in normalised UV.  Used per-channel because NTSC bandwidths
// are asymmetric.
float lowpass1(sampler2D tex, vec2 uv, float radius, int channel) {
    const int N = 5;
    float sum = 0.0;
    float wSum = 0.0;
    for (int i = -N; i <= N; ++i) {
        float w = 1.0 - abs(float(i)) / float(N + 1);
        vec3 s = texture(tex, vec2(uv.x + float(i) * radius / float(N), uv.y)).rgb;
        vec3 y = rgb2yiq(s);
        float v = (channel == 0) ? y.x : (channel == 1 ? y.y : y.z);
        sum  += v * w;
        wSum += w;
    }
    return sum / max(wSum, 1e-4);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec2 uv  = v_uv;
    float rowF = uv.y * u_resolution.y;

    // ─── Stage 4a: three-layer horizontal jitter (mechanical tape transport) ───
    //  Real VCR has slow drift from capstan wow, fast kicks from head
    //  contact variance, and very slow coarse oscillation from servo error.
    float lowDrift = (noise1d(rowF * 0.05 + u_time * 0.4) - 0.5) * 0.0025;
    float coarse   = (noise1d(rowF * 0.008 + u_time * 0.15) - 0.5) * 0.004;
    float kick     = step(0.985, hash21(vec2(floor(rowF), floor(u_time * 24.0)))) *
                     (hash21(vec2(floor(rowF) * 1.3, u_time)) - 0.5) * 0.020;
    uv.x += (lowDrift + coarse + kick) * (0.4 + 1.6 * u_line_jitter);

    // ─── Stage 4b: intermittent tracking-error band ───
    float bandT      = floor(u_time * 0.6);
    float bandActive = step(0.55, hash11(bandT));
    float bandCenter = 0.04 + 0.10 * hash11(bandT * 1.7);
    float bandHalf   = 0.004 + 0.010 * u_tracking_noise;
    float inBand     = bandActive * smoothstep(bandHalf, 0.0, abs(uv.y - bandCenter));
    uv.x += inBand * ((hash11(floor(u_time * 40.0)) - 0.5) * 0.12 * u_tracking_noise);

    // ─── Stage 4c: head-switching dirt (vertical retrace) ───
    float hsThickness = 0.01 + 0.02 * u_head_switch;
    float hsMask = smoothstep(hsThickness, 0.0, uv.y);
    float hsShift = (hash21(vec2(floor(uv.y * 600.0),
                                  floor(u_time * 60.0))) - 0.5) * 0.05;
    uv.x += hsShift * hsMask * u_head_switch;

    // ─── Stage 1: source sample → YIQ ───
    vec3 src   = texture(u_tex, uv).rgb;
    vec3 yiq0  = rgb2yiq(src);

    // ─── Stage 2: bandwidth-limit each YIQ channel by its real ratio ───
    //  Pixel radii chosen so Q is ~3× wider lowpass than I, which is ~3× I to Y.
    float blur = (0.0010 + 0.012 * u_chroma_blur);
    float Y    = lowpass1(u_tex, uv, blur * 0.30, 0);
    float I    = lowpass1(u_tex, uv, blur * 1.10, 1);
    float Q    = lowpass1(u_tex, uv, blur * 3.20, 2);

    // ─── Stage 3: chroma subcarrier phase wobble (NTSC 3.58 MHz instability) ───
    // Each row gets its own carrier phase that wanders slowly.  The IQ vector
    // is rotated by this phase before being decoded — produces hue beating.
    float phase = noise1d(rowF * 0.7 + u_time * 1.7) * 6.2831;
    float cs = cos(phase * u_color_loss);
    float sn = sin(phase * u_color_loss);
    float Iw = I * cs - Q * sn;
    float Qw = I * sn + Q * cs;

    // ─── Stage 5: color killer ───
    //  Real VCRs cut chroma below a threshold to avoid showing static-as-colour.
    //  We use chroma amplitude as the gate.
    float chromaAmp = length(vec2(Iw, Qw));
    float colorKill = smoothstep(0.02, 0.08, chromaAmp);
    Iw *= colorKill;
    Qw *= colorKill;

    vec3 col = yiq2rgb(vec3(Y, Iw, Qw));

    // ─── Stage 6: Y/C separator overshoot (ringing on edges, both sides) ───
    vec3 nL = texture(u_tex, uv + vec2(-0.005, 0.0)).rgb;
    vec3 nR = texture(u_tex, uv + vec2( 0.005, 0.0)).rgb;
    vec3 center = src;
    float dyL = dot(center - nL, vec3(0.299, 0.587, 0.114));
    float dyR = dot(nR - center, vec3(0.299, 0.587, 0.114));
    col += sign(dyR) * smoothstep(0.20, 0.55, abs(dyR)) * 0.08 * u_chroma_blur;
    col += sign(dyL) * smoothstep(0.20, 0.55, abs(dyL)) * 0.05 * u_chroma_blur;

    // ─── Stage 4d: tape DROPOUTS (magnetic patch loss) ───
    //  Real tape dropouts kill the carrier completely for a few microseconds
    //  → that line goes to noise.  Model as random horizontal slivers.
    float dropoutSeed = hash11(floor(uv.y * 480.0) + floor(u_time * 30.0));
    float dropoutMask = step(0.992, dropoutSeed) *
                       (0.4 + 0.6 * u_tracking_noise);
    if (dropoutMask > 0.0) {
        float dn = hash21(uv * u_resolution + u_time * 100.0);
        col = mix(col, vec3(dn > 0.5 ? 0.95 : 0.05), dropoutMask);
    }

    // ─── Stage 7: tape-noise hiss ───
    //  Coloured noise stronger inside tracking and head-switch bands.
    float grain = hash21(uv * u_resolution + u_time * 80.0) - 0.5;
    vec3 noise = vec3(
        grain * 0.7,
        (hash21(uv * u_resolution + vec2(11.0, u_time * 53.0)) - 0.5) * 0.25,
        (hash21(uv * u_resolution + vec2(91.0, u_time * 71.0)) - 0.5) * 0.25);
    col += noise * (0.025 + 0.08 * u_tracking_noise +
                    0.45 * (inBand + hsMask * u_head_switch));

    // ─── Stage 7b: AGC pumping + magenta cast (oxidised tape) ───
    //  Real old VHS shifts toward magenta as the cyan dye fades faster.
    float gray = dot(col, vec3(0.299, 0.587, 0.114));
    vec3 faded = mix(col, vec3(gray), 0.35);
    faded += vec3(0.025, -0.012, 0.018);
    col = mix(col, faded, u_color_loss);

    fragColor = vec4(mix(dry, col, u_mix), 1.0);
}
