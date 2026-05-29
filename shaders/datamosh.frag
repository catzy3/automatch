#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  datamosh — physically-based H.264/MPEG P-frame chain simulation
//
//  Real video compression artifacts modelled:
//    1. P-FRAME CHAIN — when keyframes (I-frames) are removed, the decoder
//       applies motion vectors from a previous frame to displace pixels,
//       then ADDS the encoded residual error.  When this chain breaks,
//       you get the characteristic "smearing" — pixels from one location
//       being dragged along motion paths into a completely different scene.
//
//       We approximate the chain by walking the motion vector backwards in
//       4 sub-steps, integrating samples with exponential decay weights.
//       Each step accumulates: P(t) = M·P(t-1) + R  where M is motion warp
//       and R is residual.
//
//    2. MACROBLOCK STRUCTURE — H.264 codes in 8×8 / 16×16 macroblocks.
//       Each block has its own motion vector that can diverge from
//       neighbours.  Per-block strength multiplier mimics encoder bit
//       allocation variance.
//
//    3. RESIDUAL ERROR ACCUMULATION — broken P-frame chains drift further
//       from ground truth each step.  Modelled as quantization-style
//       noise added per accumulation step.
//
//    4. MACROBLOCK COPY CORRUPTION — when a block's motion vector points
//       outside the reference frame, real decoders copy a NEIGHBOURING
//       block instead.  We trigger this stochastically per block.
//
//    5. I-FRAME BREAKTHROUGH — even in a broken chain, periodic
//       keyframes decode fresh.  Per-block stochastic.
//
//    6. FREEZE BLOBS — when motion-vector data is lost completely,
//       the picture freezes in patches until a new I-frame arrives.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform sampler2D u_prev;
uniform sampler2D u_flow;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_strength;
uniform float u_freeze;
uniform float u_period;
uniform float u_breakthrough;
uniform float u_jitter;
uniform float u_chaos;
uniform float u_algorithm;   // 0 = legacy P-frame chain, 1 = optical-flow mosh
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

const vec3 LUMA = vec3(0.299, 0.587, 0.114);

// ─────────────────────────────────────────────────────────────────────────────
//  ALGORITHM 1 — optical-flow datamosh (the "real" mosh)
//
//  The legacy algo treated u_flow's RG channels as a motion vector, but in
//  offline mode u_flow is just the source frame → meaningless smear.  This
//  algorithm instead ESTIMATES motion between the current frame (u_tex) and
//  the previous OUTPUT frame (u_prev) using gradient (normal-flow) optical
//  flow, then drags the accumulated previous output along that motion —
//  exactly how a decoder applies P-frame motion vectors after the I-frame is
//  dropped.  Periodic "I-frames" (u_period) and per-block breakthroughs
//  (u_breakthrough) re-inject the real frame so it never melts to mush.
//  u_freeze controls how sticky / melty the feedback is.
// ─────────────────────────────────────────────────────────────────────────────
vec3 opticalFlowMosh(vec3 cur) {
    vec2 px = 1.0 / u_resolution;

    // ── gradient optical flow: solve the brightness-constancy constraint
    //    Ix·u + Iy·v + It = 0 along the gradient (normal flow). ──
    float Lc = dot(cur, LUMA);
    float Lp = dot(texture(u_prev, v_uv).rgb, LUMA);
    float Lr = dot(texture(u_tex,  v_uv + vec2(px.x, 0.0)).rgb, LUMA);
    float Ll = dot(texture(u_tex,  v_uv - vec2(px.x, 0.0)).rgb, LUMA);
    float Lu = dot(texture(u_tex,  v_uv + vec2(0.0, px.y)).rgb, LUMA);
    float Ld = dot(texture(u_tex,  v_uv - vec2(0.0, px.y)).rgb, LUMA);
    vec2  grad = vec2((Lr - Ll) * 0.5, (Lu - Ld) * 0.5);   // per-texel
    float It   = Lc - Lp;                                  // temporal change
    vec2  flow = -It * grad / (dot(grad, grad) + 1e-3);    // texels of motion

    // clamp smear length (also caps runaway on hard scene cuts)
    float maxPix = mix(4.0, 44.0, u_strength);
    float fmag   = length(flow);
    if (fmag > maxPix) flow *= maxPix / fmag;

    // per-macroblock strength variance (16-px blocks)
    vec2  blockUV   = floor(v_uv * (u_resolution / 16.0));
    float blockSeed = hash21(blockUV + floor(u_time * 0.5));
    float gain      = mix(1.0, 0.35 + 1.7 * blockSeed, u_chaos);

    // ── drag the previous OUTPUT along the motion, multi-tap trail ──
    vec3  mosh = vec3(0.0);
    float wsum = 0.0;
    for (int k = 1; k <= 4; ++k) {
        float fk  = float(k);
        vec2  jit = vec2(hash21(v_uv * 311.0 + u_time + fk) - 0.5,
                         hash21(v_uv * 277.0 + u_time + fk + 4.3) - 0.5)
                    * px * 2.5 * u_jitter;
        vec2  sUV = v_uv - flow * px * gain * fk + jit;
        float w   = exp(-1.1 * (fk / 4.0));
        mosh += texture(u_prev, clamp(sUV, vec2(0.0), vec2(1.0))).rgb * w;
        wsum += w;
    }
    mosh /= max(wsum, 1e-4);

    // ── I-frame refresh schedule ──
    float per    = max(u_period, 0.4);
    float iframe = 1.0 - smoothstep(0.0, 0.12, fract(u_time / per));  // pulse
    float brk    = step(1.0 - u_breakthrough,
                        hash21(blockUV + floor(u_time * 3.0)));        // per-block
    float baseLeak = mix(0.30, 0.015, u_freeze);   // freeze high → melt longer
    float refresh  = clamp(max(max(iframe, brk), baseLeak), 0.0, 1.0);

    return mix(mosh, cur, refresh);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    if (int(clamp(u_algorithm, 0.0, 1.0)) == 1) {
        fragColor = vec4(mix(dry, opticalFlowMosh(dry), u_mix), 1.0);
        return;
    }

    // ── legacy algorithm 0 (kept for backward compatibility) ──
    vec2 mv  = texture(u_flow, v_uv).rg;          // optical flow vector

    // ─── 2) Macroblock parameters: 8×8 block grid ───
    vec2  blockUV   = floor(v_uv * 32.0);
    float blockSeed = hash21(blockUV + floor(u_time * 0.4));
    float blockGain = mix(1.0, 0.30 + 1.7 * blockSeed, u_chaos);

    // ─── 3) Pixel jitter (quantization-style) ───
    float j1 = (hash21(v_uv * 320.0 + u_time * 7.0) - 0.5) * 0.003 * u_jitter;
    float j2 = (hash21(v_uv * 470.0 + u_time * 5.0) - 0.5) * 0.003 * u_jitter;

    // motion-magnitude scaling: fast-moving blocks smear harder
    float motionMag   = length(mv);
    float motionBoost = 1.0 + clamp(motionMag * 6.0, 0.0, 2.0);
    float W = u_strength * blockGain * motionBoost;

    // ─── 1) P-FRAME CHAIN: walk motion vector in 4 substeps ───
    //  P_n(uv) = sum_{k=1..N} exp(-α·k) · P_(n-1)(uv - M·k)
    //  exponentially decaying weights — first step dominates, later steps
    //  contribute the long ghost tail.
    vec3 ghost = vec3(0.0);
    float wTotal = 0.0;
    float weights[4] = float[4](0.45, 0.30, 0.16, 0.09);
    for (int k = 0; k < 4; ++k) {
        float scale = float(k + 1) * 0.6;
        vec2 sUV = v_uv - mv * W * scale + vec2(j1, j2) * scale;
        // residual error accumulates per step: quantization noise added
        // to each subsequent tap, scaled by step number.
        vec3 res = vec3(
            hash21(sUV * 200.0 + float(k)) - 0.5,
            hash21(sUV * 213.0 + float(k) + 4.7) - 0.5,
            hash21(sUV * 187.0 + float(k) + 9.1) - 0.5
        ) * 0.02 * float(k) * u_chaos;
        ghost  += (texture(u_prev, sUV).rgb + res) * weights[k];
        wTotal += weights[k];
    }
    ghost /= max(wTotal, 1e-4);

    // ─── 4) Macroblock copy corruption — DEFRAGMENTED, not flat ─────
    // Real bitstream errors copy a chunk of image data from a different
    // place — pixels are SHIFTED, sometimes slightly scaled.  Never a
    // flat colour patch (that's a separate failure mode we don't model
    // here because it looks ugly).
    float corrupt = step(0.95, hash11(blockSeed * 7.1 + floor(u_time * 0.7)));
    vec2  corrOff = vec2(
        (hash11(blockSeed * 3.1) - 0.5) * 0.06,         // ±6% UV displ
        (hash11(blockSeed * 9.7) - 0.5) * 0.06
    );
    // Sub-pixel wobble so the corrupted block visibly trembles per frame
    vec2 corrWobble = vec2(
        hash11(blockSeed + u_time * 4.7) - 0.5,
        hash11(blockSeed + u_time * 5.3) - 0.5
    ) * 0.003;
    vec3 copied = texture(u_prev, v_uv + corrOff + corrWobble).rgb;
    vec3 warped = mix(ghost, copied, corrupt * u_chaos);

    // ─── 6) Freeze-blob lifecycle ───
    float bIdx     = floor(u_time / max(u_period, 0.5));
    float bT       = fract(u_time / max(u_period, 0.5));
    float bActive  = step(1.0 - u_freeze, hash11(bIdx * 11.31));
    float fadeIn   = smoothstep(0.00, 0.10, bT);
    float fadeOut  = 1.0 - smoothstep(0.85, 1.00, bT);
    float frozen   = bActive * fadeIn * fadeOut;

    // ─── 5) Per-block I-frame breakthrough ───
    float bt = step(1.0 - u_breakthrough,
                    hash21(blockUV + floor(u_time * (1.0 + 2.0 * u_chaos))));
    frozen *= mix(1.0, 0.10, bt);

    vec3 wet = mix(dry, warped, frozen);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
