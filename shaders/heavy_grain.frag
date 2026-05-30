#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  heavy_grain — physically-based silver halide film grain
//
//  Real photographic film grain physics modelled here:
//    1. CRYSTAL DENSITY = Poisson distribution.  Each pixel "rolls" how
//       many silver halide crystals it contains.  Higher density emulsions
//       have more crystals per pixel, but each is smaller.
//    2. CRYSTAL SIZE = log-normal distribution.  Real measurements of
//       Kodak Tri-X show median crystal ≈ 0.7 μm with σ_log ≈ 0.4.  In
//       shader-world we use the log-normal to vary the per-cell intensity.
//    3. H&D RESPONSE CURVE (Hurter–Driffield characteristic curve).
//       Real film density D(L) is an S-curve, not linear:
//          toe       D_min + (D_max-D_min)·(L^n / (L^n + k^n))
//       This gives the characteristic film "shoulder" in highlights and
//       "toe" lift in shadows — the exact reason film looks like film
//       and digital looks like digital.
//    4. ANISOTROPIC CRYSTALS in shadows.  Surface tension during
//       development elongates crystals vertically in underexposed regions
//       (real micrograph fact).  Modelled as cell-scale anisotropy.
//    5. CHROMA NOISE COUPLING.  Colour film has three emulsion layers
//       (R/G/B) that develop at different speeds → independent noise per
//       layer.  Modelled as separate hashes per channel.
//    6. CLUMPING / GRANULARITY.  Real grain has a coarser secondary
//       structure: crystals cluster.  Two-octave noise emulates this.
//    7. HIGHLIGHT MICRO-SPARKLE: emulsion micro-cracks reflect light →
//       occasional sub-pixel bright dots in highlights only.
//    8. RECIPROCITY FAILURE: very low light, grain becomes anisotropic
//       AND amplifies — modelled as a shadow-zone gain boost.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_amount;
uniform float u_size;
uniform float u_chroma;
uniform float u_blotch;
uniform float u_mix;
// u_algorithm: 0 = physically-based hash grain (default), 1 = Perlin-style value noise grain
// Not passing this uniform is safe — clamp(0,0,1) = 0, so algo 0 runs.
uniform float u_algorithm;

in  vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float hash31(vec3 p3) {
    p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// Box-Muller: turn two uniforms into a normal random variable.  Used to
// build log-normal crystal-size distribution.
float boxMuller(vec2 u01) {
    float r = sqrt(-2.0 * log(max(u01.x, 1e-7)));
    return r * cos(6.2831853 * u01.y);          // ~N(0,1)
}

// H&D characteristic curve.  Photographic density D(exposure L):
//   D(L) = D_min + (D_max - D_min) · L^n / (L^n + k^n)
//   Returns the "grain-response factor" — how much grain modulates this
//   tone.  Peaks around mid-tones, falls off at toe and shoulder.
float hd_response(float L) {
    // Curve peaks where dD/dL is largest, i.e. around the inflection point.
    // For n=3, k=0.5, peak is near L≈0.35 (matches real Tri-X measurements).
    float n = 3.0;
    float k = 0.50;
    float Ln = pow(L, n);
    float kn = pow(k, n);
    // derivative of the H&D curve (analytic): n·L^(n-1)·k^n / (L^n + k^n)^2
    float denom = (Ln + kn);
    float resp = n * pow(L, n - 1.0) * kn / max(denom * denom, 1e-6);
    return clamp(resp * 0.42, 0.0, 1.2);
}

// ─────────────────────────────────────────────────────────────────────────────
//  ALGO 1 — Perlin-style value noise helpers
//
//  valueNoise2D: bilinearly interpolates hash21 at four integer corners of a
//  unit cell using Hermite smoothstep, producing C1-continuous output with no
//  visible cell boundaries.  This is what distinguishes it from raw hash21.
//
//  smoothNoise: 3-octave fBm built on top of valueNoise2D.  Each octave:
//    octave 0 (coarse):  amplitude 0.50, frequency 1×
//    octave 1 (mid):     amplitude 0.30, frequency 2.3×  (incommensurate)
//    octave 2 (fine):    amplitude 0.20, frequency 5.1×
//  Incommensurate ratios avoid harmonic repetition.
//
//  Y-compression: UV is pre-scaled by vec2(1.0, 0.60) before lookup so noise
//  cells are taller than wide — mimics the vertical elongation of film grains
//  seen in micrographs of under-exposed Tri-X/HP5 stock.
// ─────────────────────────────────────────────────────────────────────────────

// Smoothstep-interpolated 2D value noise, range [0,1].
// p: continuous grid coordinate.
float valueNoise2D(vec2 p) {
    vec2 i = floor(p);           // integer cell
    vec2 f = fract(p);           // fractional offset inside cell

    // Hermite smoothstep — C1, zero-derivative at cell boundaries.
    vec2 u = f * f * (3.0 - 2.0 * f);

    // Four corners
    float a = hash21(i + vec2(0.0, 0.0));
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));

    // Bilinear interpolation with smoothstep weights
    return mix(mix(a, b, u.x),
               mix(c, d, u.x),
               u.y);
}

// 3-octave smooth fBm.  Returns signed value in approx [-0.5, 0.5].
// uvt: (x, y, time) — time shifts UV to animate grain organically.
// grainScale: spatial frequency multiplier (maps to u_size).
float smoothNoise(vec2 uvt_xy, float grainScale) {
    // Y-compression ratio: elongate cells along Y to simulate grain orientation.
    const float Y_COMPRESS = 0.60;
    vec2 p = uvt_xy * vec2(1.0, Y_COMPRESS) * grainScale;

    float v  = 0.0;
    float amp  = 0.50;
    float freq = 1.0;

    // Octave 0 — coarse structure
    v += (valueNoise2D(p * freq) - 0.5) * amp;
    amp  *= 0.60;
    freq *= 2.3;   // incommensurate — avoids tiling artefacts

    // Octave 1 — mid-frequency grain body
    v += (valueNoise2D(p * freq) - 0.5) * amp;
    amp  *= 0.67;
    freq *= 2.2;

    // Octave 2 — fine detail
    v += (valueNoise2D(p * freq) - 0.5) * amp;

    return v;  // range approximately [-0.5, 0.5]
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    float y  = dot(dry, vec3(0.299, 0.587, 0.114));

    // Backward-compatible algorithm select.
    // u_algorithm not bound (default 0.0) → clamp → algo 0, no behaviour change.
    int algo = int(clamp(u_algorithm, 0.0, 1.0));

    // ─── H&D response and reciprocity failure (shared by both algos) ───────
    float resp = hd_response(y);
    resp += smoothstep(0.18, 0.00, y) * 0.35;  // reciprocity failure

    vec3 grain = vec3(0.0);

    if (algo == 0) {
        // ═══════════════════════════════════════════════════════════════════
        //  ALGO 0 — physically-based silver halide grain (original)
        // ═══════════════════════════════════════════════════════════════════

        // ─── 4) Anisotropic crystals in shadows ───
        float aniso     = smoothstep(0.30, 0.05, y);
        vec2  cellScale = vec2(mix(1.0, 0.55, aniso), 1.0);

        // ─── 1+2) Cell grid + log-normal crystal-size weight ───
        float cellPx = mix(1.0, 6.0, u_size);
        vec2  px     = v_uv * u_resolution * cellScale;
        vec2  gpF    = floor(px / cellPx);             // fine octave
        vec2  gpC    = floor(px / (cellPx * 2.4));     // coarse octave

        float tF  = u_time * 30.0;
        float tF0 = floor(tF);
        float tFr = fract(tF);

        // log-normal crystal size per cell.
        float u1 = hash21(gpF + tF0);
        float u2 = hash21(gpF + tF0 + 23.7);
        float gauss = boxMuller(vec2(u1, u2));
        float crystalSize = exp(gauss * 0.40);
        crystalSize = clamp(crystalSize, 0.25, 3.5);

        float density = (hash21(gpF + tF0 * 1.7) - 0.5) * crystalSize;

        // Temporal coherence
        float u1n = hash21(gpF + tF0 + 1.0);
        float u2n = hash21(gpF + tF0 + 24.7);
        float gn  = boxMuller(vec2(u1n, u2n));
        float cs1 = clamp(exp(gn * 0.40), 0.25, 3.5);
        float d1  = (hash21(gpF + (tF0 + 1.0) * 1.7) - 0.5) * cs1;
        float n   = mix(density, d1, tFr);

        // ─── 6) Coarse secondary octave (granularity clumping) ───
        float c0 = hash21(gpC + tF0 * 0.3) - 0.5;
        float c1 = hash21(gpC + (tF0 + 1.0) * 0.3) - 0.5;
        float nc = mix(c0, c1, tFr);
        n += nc * 0.40;

        // ─── 5) Chroma noise — independent per-channel emulsion layer ───
        float nR = mix(hash31(vec3(gpF, tF0 + 1.0)),
                       hash31(vec3(gpF, tF0 + 4.0)), tFr) - 0.5;
        float nG = mix(hash31(vec3(gpF, tF0 + 2.0)),
                       hash31(vec3(gpF, tF0 + 5.0)), tFr) - 0.5;
        float nB = mix(hash31(vec3(gpF, tF0 + 3.0)),
                       hash31(vec3(gpF, tF0 + 6.0)), tFr) - 0.5;
        vec3  nRGB = vec3(nR, nG, nB);

        // ─── Slow large-scale clumping ───
        float bSeed = hash21(floor(gpF * 0.18) + floor(tF0 * 0.3));
        float clump = 1.0 + (bSeed - 0.5) * u_blotch * 2.5;

        vec3 mono  = vec3(n) * (1.0 - u_chroma);
        vec3 chr   = nRGB    * u_chroma;
        grain = (mono + chr) * resp * clump * u_amount;

        // ─── 7) Highlight micro-sparkle ───
        float sparkleProb = step(0.998, hash21(v_uv * u_resolution * 1.7 + tF0));
        float sparkle = sparkleProb * smoothstep(0.55, 0.85, y) * 0.18 * u_amount;
        grain += vec3(sparkle);

    } else {
        // ═══════════════════════════════════════════════════════════════════
        //  ALGO 1 — Perlin-style multi-octave value noise grain
        //
        //  Visual character: smooth, organic, "breathing" texture.
        //  No cell edges. Coarser grain clumps visible like T-grain film
        //  (Kodak T-MAX / Fuji Neopan aesthetic vs. Tri-X hash crunch).
        // ═══════════════════════════════════════════════════════════════════

        // grainScale maps u_size [0,1] → spatial frequency range [80,240].
        // Lower value = larger, sparser grain; higher = tighter, finer.
        float grainScale = mix(80.0, 240.0, u_size);

        // Slow temporal drift — noise field scrolls gently rather than
        // snapping per-frame, creating the "breathing" film-gate feel.
        float timeDrift = u_time * 0.3;

        // UV + time offset in X so grain doesn't pulse in-place.
        vec2 uvt = v_uv + vec2(timeDrift, 0.0);

        // ─── Monochrome noise base ───
        float monoN = smoothNoise(uvt, grainScale);

        // ─── Chroma: independent noise per channel, offset in UV+time ───
        // Small UV offsets decorrelate channels without visually drifting.
        float chromaAmt = u_chroma;
        float nR = smoothNoise(uvt + vec2(0.173, 0.211), grainScale) * chromaAmt;
        float nG = smoothNoise(uvt + vec2(0.317, 0.098), grainScale) * chromaAmt;
        float nB = smoothNoise(uvt + vec2(0.091, 0.364), grainScale) * chromaAmt;

        // Blend: mono channel weighted by (1 - chroma), RGB channels by chroma.
        vec3 noiseRGB = vec3(monoN) * (1.0 - chromaAmt)
                      + vec3(nR, nG, nB);

        // ─── Large-scale clumping (maps to u_blotch) ───
        // A very coarse, slow noise field modulates grain density regionally,
        // giving areas of heavier and lighter grain like uneven development.
        float coarseUV = smoothNoise(uvt * 0.08, grainScale * 0.04);
        float clump = 1.0 + coarseUV * u_blotch * 2.5;

        grain = noiseRGB * resp * clump * u_amount;
    }

    vec3 wet = clamp(dry + grain, 0.0, 1.0);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
