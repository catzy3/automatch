#version 330 core
// Port of v1's datamosh.frag — "optical-flow mosh" (algorithm 1): the real
// P-frame-chain look. Estimates motion between the current frame and the
// previous OUTPUT (u_prev), drags the accumulated output along that flow in a
// 4-tap trail, with per-macroblock strength variance and an I-frame / block
// breakthrough refresh schedule so it never melts to mush.
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform sampler2D u_prev;   // previous chain output (feedback)
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // strength      (smear length)
uniform float u_p1; // freeze        (stickiness / melt time)
uniform float u_p2; // period        (I-frame refresh, seconds)
uniform float u_p3; // breakthrough  (per-block fresh decode)
uniform float u_p4; // jitter        (tap dither)
uniform float u_p5; // chaos         (macroblock variance)

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
const vec3 LUMA = vec3(0.299, 0.587, 0.114);

void main() {
    vec3 cur = texture(u_tex, v_uv).rgb;
    vec2 px = 1.0 / max(u_resolution, vec2(1.0));
    float strength = u_p0, freeze = u_p1, period = u_p2;
    float breakthrough = u_p3, jitter = u_p4, chaos = u_p5;

    // ── gradient (normal-flow) optical flow between current & previous ──
    float Lc = dot(cur, LUMA);
    float Lp = dot(texture(u_prev, v_uv).rgb, LUMA);
    float Lr = dot(texture(u_tex, v_uv + vec2(px.x, 0.0)).rgb, LUMA);
    float Ll = dot(texture(u_tex, v_uv - vec2(px.x, 0.0)).rgb, LUMA);
    float Lu = dot(texture(u_tex, v_uv + vec2(0.0, px.y)).rgb, LUMA);
    float Ld = dot(texture(u_tex, v_uv - vec2(0.0, px.y)).rgb, LUMA);
    vec2 grad = vec2((Lr - Ll) * 0.5, (Lu - Ld) * 0.5);
    float It = Lc - Lp;
    vec2 flow = -It * grad / (dot(grad, grad) + 1e-3);

    float maxPix = mix(4.0, 44.0, clamp(strength, 0.0, 1.0));
    float fmag = length(flow);
    if (fmag > maxPix) flow *= maxPix / fmag;

    vec2 blockUV = floor(v_uv * (u_resolution / 16.0));
    float blockSeed = hash21(blockUV + floor(u_time * 0.5));
    float gain = mix(1.0, 0.35 + 1.7 * blockSeed, clamp(chaos, 0.0, 1.0));

    // ── drag previous output along the motion (4-tap decaying trail) ──
    vec3 mosh = vec3(0.0);
    float wsum = 0.0;
    for (int k = 1; k <= 4; ++k) {
        float fk = float(k);
        vec2 jit = vec2(hash21(v_uv * 311.0 + u_time + fk) - 0.5,
                        hash21(v_uv * 277.0 + u_time + fk + 4.3) - 0.5)
                   * px * 2.5 * jitter;
        vec2 sUV = v_uv - flow * px * gain * fk + jit;
        float w = exp(-1.1 * (fk / 4.0));
        mosh += texture(u_prev, clamp(sUV, vec2(0.0), vec2(1.0))).rgb * w;
        wsum += w;
    }
    mosh /= max(wsum, 1e-4);

    // ── I-frame / breakthrough refresh so it doesn't melt forever ──
    float per = max(period, 0.4);
    float iframe = 1.0 - smoothstep(0.0, 0.12, fract(u_time / per));
    float brk = step(1.0 - breakthrough, hash21(blockUV + floor(u_time * 3.0)));
    float baseLeak = mix(0.30, 0.015, clamp(freeze, 0.0, 1.0));
    float refresh = clamp(max(max(iframe, brk), baseLeak), 0.0, 1.0);

    vec3 outc = mix(mosh, cur, refresh);
    fragColor = vec4(mix(cur, outc, clamp(u_mix, 0.0, 1.0)), 1.0);
}
