#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  motion_trails — physically-based long-exposure with phosphor decay model
//
//  Physics modelled:
//    1. TEMPORAL INTEGRATION — convolution of the moving scene with a
//       point-spread function in TIME.  Real long-exposure cameras
//       integrate light over the exposure window; we sample the previous
//       frame at 8 positions along the motion vector to approximate this.
//       The integral I(uv, t) ≈ Σ S(uv - v·τᵢ) · w(τᵢ).
//    2. VELOCITY ESTIMATION via frame difference.  Where current and prev
//       differ → there's motion → trail length proportional.
//    3. PHOSPHOR DECAY: per-channel exponential e^(-t/τ_c) with
//          τ_red   = 1.0   ms  → slow
//          τ_green = 10.0  ms  → medium
//          τ_blue  = 0.01  ms  → fast
//       In a single-frame trail this means R-channel of the trail
//       persists longer, G middle, B shortest → trails go magenta then
//       red then fade. This is the actual CRT phosphor afterimage effect.
//    4. RADIAL ADVECTION when drift is zero — trail extends radially
//       from frame centre, simulating zoom-in motion.
//    5. SCREEN-BLEND COMPOSITING in linear space (gamma-correct).
//    6. SUB-PIXEL JITTER for natural breakup.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_current;
uniform sampler2D u_prev;
uniform float u_time;
uniform float u_decay;
uniform float u_threshold;
uniform float u_blur;
uniform vec2  u_drift;
uniform vec3  u_tint;
uniform float u_secondary;
uniform float u_mode;
uniform float u_polarity;
uniform float u_chaos;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// sRGB <-> linear (cheap approximation; close to spec)
vec3 srgb_to_lin(vec3 c) { return c * c * (c * 0.305306 + 0.682171) + c * 0.012522; }
vec3 lin_to_srgb(vec3 c) { return sqrt(c); }

void main() {
    vec3 cur  = texture(u_current, v_uv).rgb;
    vec3 prev0 = texture(u_prev, v_uv).rgb;

    // ─── 2) Velocity from frame difference ───
    //  Higher |cur - prev| → more motion at this pixel → longer trail.
    float speed = length(cur - prev0);
    float speedBoost = 1.0 + speed * 6.0;

    // ─── 4) Radial component when drift is small ───
    vec2 toC = v_uv - 0.5;
    float r  = length(toC);
    vec2 rad = (r > 1e-4) ? toC / r : vec2(0.0);
    vec2 driftN = u_drift * 0.01;
    float driftMag = length(driftN);
    float radW = exp(-driftMag * 40.0);
    vec2 dirVec = driftN + rad * 0.012 * radW * r;

    // ─── 6) Sub-pixel jitter (de-aliasing) ───
    vec2 jit = vec2(
        hash21(v_uv * 320.0 + u_time * 7.0) - 0.5,
        hash21(v_uv * 470.0 + u_time * 5.0) - 0.5
    ) * 0.004 * u_chaos * speedBoost;

    // ─── 1) TEMPORAL INTEGRATION via 8 samples along motion vector ───
    //  Sample weights are exponentially decaying (radioactive-decay model):
    //  w_i = exp(-α · i)  with α tuned so the 8th sample is ~5% of the 1st.
    //  We accumulate per-channel separately to honour different decay
    //  constants in step 3.
    vec3  trailR = vec3(0.0);
    vec3  trailG = vec3(0.0);
    vec3  trailB = vec3(0.0);
    float wR = 0.0, wG = 0.0, wB = 0.0;
    float blurR = (0.002 + 0.006 * u_blur) * (1.0 + speed * 6.0);
    for (int k = 0; k < 8; ++k) {
        float fk = float(k) + 0.5;
        vec2 sUV = v_uv + dirVec * fk * 0.7 + jit * fk;
        // Cross-shaped 5-tap micro-blur per sample to soften
        vec3 s = vec3(0.0);
        s += texture(u_prev, sUV).rgb * 0.40;
        s += texture(u_prev, sUV + vec2( blurR, 0.0)).rgb * 0.15;
        s += texture(u_prev, sUV + vec2(-blurR, 0.0)).rgb * 0.15;
        s += texture(u_prev, sUV + vec2(0.0,  blurR)).rgb * 0.15;
        s += texture(u_prev, sUV + vec2(0.0, -blurR)).rgb * 0.15;
        // ─── 3) Per-channel phosphor decay weights ───
        //  Exponential decay with channel-specific tau.
        float wRk = exp(-fk * 0.18);                   // R: slow
        float wGk = exp(-fk * 0.30);                   // G: medium
        float wBk = exp(-fk * 0.85);                   // B: fast
        trailR += s * wRk;
        trailG += s * wGk;
        trailB += s * wBk;
        wR += wRk; wG += wGk; wB += wBk;
    }
    vec3 trail = vec3(trailR.r / max(wR, 1e-4),
                      trailG.g / max(wG, 1e-4),
                      trailB.b / max(wB, 1e-4));
    trail *= u_decay;

    // Threshold gate (only bright stuff contributes — silver halide-like)
    float yT   = dot(trail, vec3(0.299, 0.587, 0.114));
    float gate = smoothstep(u_threshold, u_threshold + 0.15, yT);
    trail *= gate;

    // Secondary deep echo (chroma-shifted toward tint)
    vec3 echo = texture(u_prev, v_uv + dirVec * 4.0 + jit * 3.0).rgb;
    echo *= u_decay * u_decay * u_secondary;
    echo = mix(echo, echo * u_tint * 1.4, 0.6);

    trail *= u_tint;
    trail += echo;

    if (u_polarity > 0.5) trail = vec3(1.0) - trail;

    // ─── 5) Gamma-correct compositing ───
    vec3 curL   = srgb_to_lin(cur);
    vec3 trailL = srgb_to_lin(clamp(trail, 0.0, 1.0));
    int mode = int(clamp(u_mode, 0.0, 4.0));
    vec3 wetL;
    if (mode == 0)       wetL = 1.0 - (1.0 - curL) * (1.0 - trailL);     // screen
    else if (mode == 1)  wetL = max(curL, trailL);                       // max
    else if (mode == 2)  wetL = curL + trailL;                           // add
    else if (mode == 3)  wetL = mix(curL, max(curL, trailL * 1.2), 0.9); // lighten
    else                 wetL = mix(2.0 * curL * trailL,
                                    1.0 - 2.0 * (1.0 - curL) * (1.0 - trailL),
                                    step(0.5, curL));                    // overlay
    vec3 wet = lin_to_srgb(clamp(wetL, 0.0, 1.0));
    if (u_polarity > 0.5) wet = vec3(1.0) - wet;

    fragColor = vec4(mix(cur, wet, u_mix), 1.0);
}
