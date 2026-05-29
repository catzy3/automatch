#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  pixel_sort — linear pixel sorting (+ one DV25 block-scramble holdover)
//
//  Rewritten 2026-05: the blocky DV25 codec-damage algorithms (stuck AC,
//  chroma tear, dropout concealment) were removed — they produced square
//  artifacts the user dislikes.  What remains:
//
//    0 = Kim-Asendorf horizontal   (linear, threshold-segmented streaks)
//    1 = Kim-Asendorf vertical
//    2 = Bitonic merge sort horizontal (fixed-length sorted bands)
//    3 = Bitonic merge sort vertical
//    4 = DV25 block scramble       (the one block algorithm kept)
//
//  KA (0/1) vs Bitonic (2/3):
//    • KA walks out from each pixel until featureValue drops below
//      u_threshold, sorts that *variable* segment, rank-matches → broken,
//      organic streaks.
//    • Bitonic snaps the row/column to a grid of length `u_length` px,
//      fully sorts that *fixed* band with a bitonic network, and lays the
//      pixels back in sorted order → clean continuous gradient bands.
//      Not threshold-gated, so it sorts more aggressively / completely.
//
//  Uniform mapping (kept for backward compatibility):
//    u_threshold    KA segment cutoff / block-scramble damage gate
//    u_length       *** РАЗМЕР *** sort band length (0..1 → 8..96 px);
//                   for block scramble → block size (0..1 → 4..28 px)
//    u_direction    KA/scramble scan direction (-1 left, 0 both, +1 right)
//    u_band_density fraction of rows/columns (or blocks) affected
//    u_band_jitter  temporal noise in the affected-line pattern
//    u_band_speed   animation speed of the line/band selection
//    u_length_var   per-band length jitter (block scramble only)
//    u_edge_fade    soft band edges (block scramble only)
//    u_color_mode   governing feature: 0=luma 1=R 2=G 3=B 4=saturation
//    u_axis         (unused — direction is encoded in u_algorithm now)
//    u_algorithm    0..4 selector (see above)
//    u_chromatic    4:1:1 chroma subsample offset intensity
//    u_blend        block-scramble blend mode (0 replace 1 screen 2 lighten 3 darken)
//    u_mix          dry/wet
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_threshold;
uniform float u_length;
uniform float u_direction;
uniform float u_band_density;
uniform float u_band_jitter;
uniform float u_band_speed;
uniform float u_length_var;
uniform float u_edge_fade;
uniform float u_color_mode;
uniform float u_axis;
uniform float u_algorithm;
uniform float u_chromatic;
uniform float u_blend;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float featureValue(vec3 c) {
    int m = int(clamp(u_color_mode, 0.0, 4.0));
    if (m == 0) return dot(c, vec3(0.299, 0.587, 0.114));
    if (m == 1) return c.r;
    if (m == 2) return c.g;
    if (m == 3) return c.b;
    float mx = max(c.r, max(c.g, c.b));
    float mn = min(c.r, min(c.g, c.b));
    return mx - mn;                              // saturation / chroma
}

// per-line gate: is this row/column selected for sorting this frame?
bool lineActive(bool vertical) {
    float tClock = floor(u_time * mix(0.5, 8.0, u_band_speed) * (0.1 + u_band_jitter));
    float lineIdx = vertical ? floor(v_uv.x * u_resolution.x)
                             : floor(v_uv.y * u_resolution.y);
    return hash21(vec2(lineIdx, tClock)) <= u_band_density;
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    int algo = int(clamp(u_algorithm, 0.0, 4.0));

    // ── Kim-Asendorf classic sort (0 horizontal / 1 vertical) ──
    if (algo == 0 || algo == 1) {
        bool vertical = (algo == 1);
        vec2 axis     = vertical ? vec2(0.0, 1.0) : vec2(1.0, 0.0);

        if (!lineActive(vertical)) { fragColor = vec4(dry, 1.0); return; }

        float maxLen = mix(8.0, 96.0, clamp(u_length, 0.0, 1.0));

        float startT = 0.0, endT = 0.0;
        for (int k = 1; k <= 96; ++k) {
            if (float(k) > maxLen) break;
            vec2 sUV = v_uv - axis * float(k) / u_resolution;
            if (sUV.x < 0.0 || sUV.x > 1.0 || sUV.y < 0.0 || sUV.y > 1.0) break;
            if (featureValue(texture(u_tex, sUV).rgb) < u_threshold) break;
            startT = float(k);
        }
        for (int k = 1; k <= 96; ++k) {
            if (float(k) > maxLen) break;
            vec2 sUV = v_uv + axis * float(k) / u_resolution;
            if (sUV.x < 0.0 || sUV.x > 1.0 || sUV.y < 0.0 || sUV.y > 1.0) break;
            if (featureValue(texture(u_tex, sUV).rgb) < u_threshold) break;
            endT = float(k);
        }

        if (startT + endT < 2.0) { fragColor = vec4(dry, 1.0); return; }

        const int N = 8;
        vec3  samples[N];
        float keys[N];
        for (int i = 0; i < N; ++i) {
            float t  = float(i) / float(N - 1);
            float px = mix(-startT, endT, t);
            vec2 sUV = v_uv + axis * px / u_resolution;
            samples[i] = texture(u_tex, sUV).rgb;
            keys[i]    = featureValue(samples[i]);
        }
        for (int i = 0; i < N - 1; ++i)
            for (int j = 0; j < N - 1; ++j)
                if (j < N - 1 - i && keys[j] > keys[j + 1]) {
                    float tk = keys[j];   keys[j] = keys[j + 1];     keys[j + 1] = tk;
                    vec3  tv = samples[j]; samples[j] = samples[j + 1]; samples[j + 1] = tv;
                }

        float myKey = featureValue(dry);
        int   rank  = 0;
        for (int i = 0; i < N; ++i) if (keys[i] < myKey) rank++;
        rank = clamp(rank, 0, N - 1);

        vec3 wet = samples[rank];
        if (u_chromatic > 0.001) {
            float chOff = u_chromatic * (4.0 / u_resolution.x);
            wet.r = texture(u_tex, v_uv + vec2(chOff, 0.0)).r;
            wet.b = texture(u_tex, v_uv - vec2(chOff, 0.0)).b;
        }
        fragColor = vec4(mix(dry, wet, u_mix), 1.0);
        return;
    }

    // ── Parallel bitonic merge sort (2 horizontal / 3 vertical) ──
    // Grid-snapped fixed-length band, fully sorted, pixels laid back in
    // sorted order across the band → clean continuous gradient streaks.
    if (algo == 2 || algo == 3) {
        bool vertical = (algo == 3);
        vec2 axis     = vertical ? vec2(0.0, 1.0) : vec2(1.0, 0.0);

        if (!lineActive(vertical)) { fragColor = vec4(dry, 1.0); return; }

        vec2  pix       = v_uv * u_resolution;
        float lineCoord = vertical ? pix.y : pix.x;
        float bandLen   = mix(8.0, 96.0, clamp(u_length, 0.0, 1.0));
        float bandStart = floor(lineCoord / bandLen) * bandLen;

        const int N = 16;                       // bitonic needs power of two
        vec3  samples[N];
        float keys[N];
        bool  any = false;
        for (int i = 0; i < N; ++i) {
            float fpos = bandStart + (float(i) + 0.5) / float(N) * bandLen;
            vec2  sUV  = vertical ? vec2(v_uv.x, fpos / u_resolution.y)
                                  : vec2(fpos / u_resolution.x, v_uv.y);
            sUV = clamp(sUV, vec2(0.0), vec2(1.0));
            samples[i] = texture(u_tex, sUV).rgb;
            keys[i]    = featureValue(samples[i]);
            if (keys[i] >= u_threshold) any = true;
        }
        if (!any) { fragColor = vec4(dry, 1.0); return; }

        // ascending bitonic sorting network over N=16
        bool ascending = (u_direction >= 0.0);
        for (int k = 2; k <= N; k <<= 1) {
            for (int j = k >> 1; j > 0; j >>= 1) {
                for (int i = 0; i < N; ++i) {
                    int ixj = i ^ j;
                    if (ixj > i) {
                        bool up = ((i & k) == 0) == ascending;
                        if ((keys[i] > keys[ixj]) == up) {
                            float tk = keys[i];   keys[i] = keys[ixj];     keys[ixj] = tk;
                            vec3  tv = samples[i]; samples[i] = samples[ixj]; samples[ixj] = tv;
                        }
                    }
                }
            }
        }

        // current pixel's slot = its spatial position within the band
        float myPos = (lineCoord - bandStart) / bandLen;     // 0..1
        int   slot  = clamp(int(myPos * float(N)), 0, N - 1);
        vec3  wet   = samples[slot];

        if (u_chromatic > 0.001) {
            float chOff = u_chromatic * (4.0 / u_resolution.x);
            wet.r = texture(u_tex, v_uv + vec2(chOff, 0.0)).r;
            wet.b = texture(u_tex, v_uv - vec2(chOff, 0.0)).b;
        }
        fragColor = vec4(mix(dry, wet, u_mix), 1.0);
        return;
    }

    // ── DV25 block scramble (4) — the one block algorithm kept ──
    // Real DV decoder errors pull pixel data from the wrong place in the
    // bitstream → a shifted/rescaled chunk of the SAME image relocated into
    // the wrong block.  Block size is now driven by u_length (РАЗМЕР), so it
    // can shrink to 4 px instead of the old fixed 8/16.
    float blockSize = floor(mix(4.0, 28.0, clamp(u_length, 0.0, 1.0)));
    blockSize = max(blockSize, 2.0);
    vec2  pix       = v_uv * u_resolution;
    vec2  blockIdx  = floor(pix / blockSize);
    vec2  blockOrig = blockIdx * blockSize / u_resolution;
    vec2  inBlockPx = pix - blockIdx * blockSize;
    vec2  inBlock01 = inBlockPx / blockSize;

    float tClock    = floor(u_time * mix(0.5, 8.0, u_band_speed) * (0.1 + u_band_jitter));
    float blockSeed = hash21(blockIdx + tClock * 0.37);

    float blockFeature = featureValue(texture(u_tex, blockOrig + 0.5 / u_resolution).rgb);
    float featureGate  = smoothstep(u_threshold, u_threshold + 0.20, blockFeature);
    float densityGate  = step(1.0 - u_band_density, blockSeed);
    if (featureGate * densityGate < 0.5) { fragColor = vec4(dry, 1.0); return; }

    float lenJ = (hash11(blockSeed + 17.0) - 0.5) * u_length_var;
    float lenN = clamp(u_length + lenJ, 0.0, 1.0);

    vec2 displ = vec2(hash11(blockSeed * 3.7) - 0.5,
                      hash11(blockSeed * 9.1) - 0.5)
                 * mix(2.0, 6.0, lenN) * blockSize / u_resolution;

    float wobbleSeed = blockSeed * 1.7 + floor(u_time * 12.0) * 0.31;
    vec2 wobble = vec2(hash11(wobbleSeed) - 0.5, hash11(wobbleSeed + 7.3) - 0.5)
                  * 0.003 * u_band_jitter;

    vec2 inBlockN = (inBlock01 - 0.5);
    float bScale  = 1.0 + (hash11(blockSeed * 5.1) - 0.5) * 0.25;
    vec2 scaled   = (inBlockN * bScale + 0.5);
    vec2 scaledUv = blockOrig + scaled * blockSize / u_resolution;

    vec2 sampleUv = scaledUv + displ + wobble;
    vec3 damagedCol = texture(u_tex, clamp(sampleUv, vec2(0.001), vec2(0.999))).rgb;

    if (u_chromatic > 0.001) {
        float chOff = u_chromatic * (4.0 / u_resolution.x);
        damagedCol.r = texture(u_tex, v_uv + vec2(chOff, 0.0)).r;
        damagedCol.b = texture(u_tex, v_uv - vec2(chOff, 0.0)).b;
    }

    float edge = 1.0;
    if (u_edge_fade > 0.001) {
        float e = u_edge_fade * 0.5;
        edge  = smoothstep(0.0, e, inBlock01.x) *
                (1.0 - smoothstep(1.0 - e, 1.0, inBlock01.x)) *
                smoothstep(0.0, e, inBlock01.y) *
                (1.0 - smoothstep(1.0 - e, 1.0, inBlock01.y));
    }

    int blend = int(clamp(u_blend, 0.0, 3.0));
    vec3 wet;
    if      (blend == 0) wet = damagedCol;
    else if (blend == 1) wet = 1.0 - (1.0 - dry) * (1.0 - damagedCol);
    else if (blend == 2) wet = max(dry, damagedCol);
    else                 wet = min(dry, damagedCol);

    wet = mix(dry, wet, edge);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
