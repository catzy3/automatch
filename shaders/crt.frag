#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  CRT — physically-based cathode-ray tube simulation
//
//  Real CRT physics modelled here:
//    1. Magnetic deflection — beam follows curved path → barrel distortion
//       on screen.  Quartic polynomial like Snell's-law lens.
//    2. THREE electron guns (R, G, B) firing at slight angles → naturally
//       miss-convergence which grows with distance from screen centre.
//       Radial AND tangential components (Earth's magnetic field bends
//       deflection by latitude → real CRTs are tuned to a specific city).
//    3. Electron beam SPOT = Gaussian, not a point.  Beam halation = the
//       Gaussian PSF convolution gives natural anti-aliasing and bloom.
//    4. SHADOW MASK / APERTURE GRILLE: physical mask between gun and
//       phosphor.  Aperture grille = vertical wires (Trinitron),
//       shadow mask = staggered dots (most NTSC TVs).
//    5. PHOSPHOR PERSISTENCE: P22 phosphors have different decay times:
//       red:    ~1   ms    (slow)
//       green:  ~10  ms    (medium)
//       blue:   ~10  μs    (very fast)
//       Visible as the colour-tinted ghost on fast motion.
//    6. Black-level pumping: DC restoration in the chroma decoder couples
//       average luma to displayed brightness.  Sudden bright frame →
//       overall lift in shadows for a few frames.
//    7. Edge bloom: the gaussian spot is wider in highlights because the
//       beam is more energetic → ABL (Automatic Brightness Limiter)
//       defocus.
//    8. Vertical retrace: very rarely, the beam picks up sync error and
//       you see a single-pixel-tall offset frame ghost.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform sampler2D u_prev;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_scanline;
uniform float u_mask;
uniform float u_curvature;
uniform float u_vignette;
uniform float u_persistence;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

// Magnetic-deflection barrel distortion.  Real CRTs use a quartic
// polynomial fit to keep the centre flat and pull only the corners.
vec2 magnetic_deflect(vec2 uv, float k) {
    vec2 c = uv - 0.5;
    float r2 = dot(c, c);
    // 1 + k₁·r² + k₂·r⁴ — quartic produces the characteristic "pincushion"
    // when k₂ > 0 (real CRTs).
    float distort = 1.0 + r2 * 0.45 * k + r2 * r2 * 0.20 * k;
    return c * distort + 0.5;
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec2 uv  = magnetic_deflect(v_uv, u_curvature);

    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        fragColor = vec4(mix(dry, vec3(0.0), u_mix), 1.0);
        return;
    }

    vec2 px = 1.0 / u_resolution;

    // ─── 2) Three-beam convergence error (radial + tangential) ───
    //  Each gun is at a slightly different angle → each colour channel
    //  needs its own sample position.  Real CRTs have factory-tuned
    //  static convergence (centre) + magnetic-field-induced dynamic
    //  convergence (varies across screen).
    vec2  c       = uv - 0.5;
    float radial  = length(c);
    vec2  rDir    = (radial > 1e-4) ? c / radial : vec2(1.0, 0.0);
    vec2  tDir    = vec2(-rDir.y, rDir.x);
    float conv    = (0.0010 + 0.0050 * radial) * u_curvature;
    float convT   = 0.0008 * u_curvature * sin(uv.y * 6.28);

    // ─── 3) Gaussian beam-spot sampling: 5-tap per channel ───
    //  Approximates beam-spot Gaussian sigma ≈ 0.7 px.
    vec3 beam;
    {
        vec2 cR = uv + rDir *  conv + tDir * convT;
        vec2 cG = uv;
        vec2 cB = uv - rDir *  conv - tDir * convT;
        // 5-tap gaussian (sigma=0.7 px) per beam centre
        float w0 = 0.405, w1 = 0.244, w2 = 0.054;
        beam.r = texture(u_tex, cR).r * w0
               + texture(u_tex, cR + vec2( px.x, 0.0)).r * w1
               + texture(u_tex, cR - vec2( px.x, 0.0)).r * w1
               + texture(u_tex, cR + vec2(0.0,  px.y)).r * w2
               + texture(u_tex, cR - vec2(0.0,  px.y)).r * w2;
        beam.g = texture(u_tex, cG).g * w0
               + texture(u_tex, cG + vec2( px.x, 0.0)).g * w1
               + texture(u_tex, cG - vec2( px.x, 0.0)).g * w1
               + texture(u_tex, cG + vec2(0.0,  px.y)).g * w2
               + texture(u_tex, cG - vec2(0.0,  px.y)).g * w2;
        beam.b = texture(u_tex, cB).b * w0
               + texture(u_tex, cB + vec2( px.x, 0.0)).b * w1
               + texture(u_tex, cB - vec2( px.x, 0.0)).b * w1
               + texture(u_tex, cB + vec2(0.0,  px.y)).b * w2
               + texture(u_tex, cB - vec2(0.0,  px.y)).b * w2;
    }
    vec3 col = beam;

    // ─── 7) ABL halation: bright spots defocus more → halo glow ───
    //  Separable 13-tap large-radius blur, gated by highlight luma.
    vec3 halo = vec3(0.0);
    float wts[7] = float[7](0.196, 0.175, 0.121, 0.066, 0.028, 0.009, 0.002);
    float wSum = 0.0;
    for (int i = -6; i <= 6; ++i) {
        float w = wts[abs(i)];
        halo += texture(u_tex, uv + vec2(float(i) * px.x * 2.0, 0.0)).rgb * w;
        halo += texture(u_tex, uv + vec2(0.0, float(i) * px.y * 2.0)).rgb * w;
        wSum += 2.0 * w;
    }
    halo /= max(wSum, 1e-4);
    float haloLuma = dot(halo, vec3(0.299, 0.587, 0.114));
    float haloMask = smoothstep(0.55, 1.0, haloLuma);
    col += halo * haloMask * 0.45;

    // ─── 5) Per-channel phosphor persistence (R slow, G medium, B fast) ───
    //  P22 phosphor decay characteristics: red lags longer than blue.
    //  Real CRTs show greenish ghost on fast motion.
    if (u_persistence > 0.001) {
        vec3 prev = texture(u_prev, v_uv).rgb;
        // per-channel retention: R=0.94, G=0.88, B=0.55 multiplied by u_persistence
        vec3 retention = vec3(0.94, 0.88, 0.55) * u_persistence;
        col = max(col, prev * retention);
    }

    // ─── 8) Vertical-hold sync ghost (occasional) ───
    float vhGate = step(0.96, fract(u_time * 0.13));
    if (vhGate > 0.0) {
        vec3 vhost = texture(u_tex, uv + vec2(0.0, px.y * 4.0)).rgb;
        col = mix(col, max(col, vhost * 0.40), vhGate * 0.5);
    }

    // ─── Scanlines: pinned to fixed visual line count (480) ───
    //  Mod by line number not screen Y, to avoid moire with the source.
    float scanY = uv.y * 480.0 * 3.14159;
    float scan  = 0.5 + 0.5 * sin(scanY);
    scan = pow(scan, 1.7);
    col *= mix(1.0, scan, u_scanline * 0.7);

    // ─── 4) Phosphor mask: aperture-grille vs shadow-mask ───
    //  u_mask ∈ [0, 0.5]: pure aperture grille (vertical stripes)
    //  u_mask ∈ [0.5, 1]: cross-fades to shadow mask (staggered triad dots)
    float stripePos = mod(uv.x * 800.0, 3.0);
    vec3 stripe;
    if (stripePos < 1.0)       stripe = vec3(1.25, 0.85, 0.85);
    else if (stripePos < 2.0)  stripe = vec3(0.85, 1.25, 0.85);
    else                       stripe = vec3(0.85, 0.85, 1.25);

    // shadow mask: row-shifted RGB triads
    vec2 dotPos    = uv * vec2(720.0, 240.0);
    float rowShift = step(0.5, fract(dotPos.y));
    float dotPx    = mod(dotPos.x + rowShift * 0.5, 1.0);
    vec3 dotMask;
    if      (dotPx < 0.333) dotMask = vec3(1.30, 0.80, 0.80);
    else if (dotPx < 0.667) dotMask = vec3(0.80, 1.30, 0.80);
    else                    dotMask = vec3(0.80, 0.80, 1.30);

    float modeBlend = smoothstep(0.45, 0.55, u_mask);
    vec3  mask      = mix(stripe, dotMask, modeBlend);
    col *= mix(vec3(1.0), mask, clamp(u_mask, 0.0, 1.0) * 0.6);

    // ─── 6) Black-level pumping: average luma couples to brightness ───
    //  Subtle but real — bright frames cause the whole image to lift slightly.
    //  We approximate by sampling four corners and using their mean.
    vec3 corners = (texture(u_tex, vec2(0.1, 0.1)).rgb +
                    texture(u_tex, vec2(0.9, 0.1)).rgb +
                    texture(u_tex, vec2(0.1, 0.9)).rgb +
                    texture(u_tex, vec2(0.9, 0.9)).rgb) * 0.25;
    float avgL = dot(corners, vec3(0.299, 0.587, 0.114));
    col += (avgL - 0.5) * 0.05;            // ±2.5% lift swing

    // ─── Vignette ───
    float vig = 1.0 - dot(c, c) * 1.4 * u_vignette;
    col *= clamp(vig, 0.0, 1.0);

    fragColor = vec4(mix(dry, col, u_mix), 1.0);
}
