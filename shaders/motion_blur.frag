#version 330 core
// ─────────────────────────────────────────────────────────────────────────────
//  motion_blur — directional and radial (zoom) blur
//
//  Two modes blended via u_radial:
//
//    DIRECTIONAL (u_radial = 0)
//      N samples are taken along the vector defined by u_angle, spaced
//      evenly over the total length u_length (in UV units).  All samples
//      receive equal weight — the result is a simple box blur along the
//      motion direction, which closely matches a camera with linear motion
//      during the exposure window.
//
//    RADIAL / ZOOM BLUR (u_radial = 1)
//      N samples are taken along the vector from u_center → current pixel,
//      scaled so the farthest sample is offset by u_length in UV units.
//      This simulates a zoom on the sensor during exposure.
//
//    u_radial in (0,1) cross-fades the two results, letting you morph
//    smoothly between a motion-vector smear and a zoom-burst effect.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2      u_resolution;
uniform float     u_time;       // unused here, reserved for animated blur
uniform float     u_mix;        // 0 = dry, 1 = full wet

// ── effect-specific uniforms ─────────────────────────────────────────────────
uniform int   u_samples;    // 4..16  — number of accumulation taps
uniform float u_length;     // 0..0.03 — blur extent in UV units
uniform float u_angle;      // 0..6.28 — direction of blur (radians)
uniform float u_radial;     // 0..1    — 0 = directional, 1 = radial/zoom
uniform float u_center_x;   // 0..1    — radial centre (UV X)
uniform float u_center_y;   // 0..1    — radial centre (UV Y)

in  vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // ── guard against degenerate inputs ──────────────────────────────────────
    int  taps   = clamp(u_samples, 4, 16);
    float len   = clamp(u_length, 0.0, 0.05);  // hard cap prevents texture fetch OOB artefacts

    if (len < 1e-5) {
        // zero-length blur — pass through unchanged
        fragColor = vec4(dry, 1.0);
        return;
    }

    // ── directional blur ─────────────────────────────────────────────────────
    //  Step vector along the fixed angle, spanning ±half-length around the
    //  current pixel so the blur is centred (no directional bias at u_mix).
    vec2 dirVec = vec2(cos(u_angle), sin(u_angle)) * len;

    vec3  dirAcc  = vec3(0.0);
    float dirWsum = 0.0;
    for (int i = 0; i < taps; ++i) {
        // t goes from -0.5 to +0.5 so the kernel is centred on the pixel
        float t   = (float(i) / float(taps - 1)) - 0.5;
        vec2  suv = v_uv + dirVec * t;
        suv       = clamp(suv, vec2(0.0), vec2(1.0));   // clamp, not wrap
        dirAcc   += texture(u_tex, suv).rgb;
        dirWsum  += 1.0;
    }
    vec3 dirResult = dirAcc / max(dirWsum, 1e-4);

    // ── radial / zoom blur ────────────────────────────────────────────────────
    //  Step from the current pixel TOWARD the centre, so the smear always
    //  points radially inward (zoom-in appearance).  Samples span 0..len
    //  rather than ±half so the current pixel position is the "tip" of the
    //  streak and the centre remains sharp.
    vec2 centre  = vec2(u_center_x, u_center_y);
    vec2 radDir  = v_uv - centre;                       // points away from centre
    float dist   = length(radDir);
    // normalise: if pixel is exactly at centre, step is zero → no blur there,
    // which is correct (the focal point of a zoom should be sharp).
    vec2 radStep = (dist > 1e-5) ? (radDir / dist) * len : vec2(0.0);

    vec3  radAcc  = vec3(0.0);
    float radWsum = 0.0;
    for (int i = 0; i < taps; ++i) {
        // t goes from 0 to 1: tap 0 is current pixel, tap N-1 is fully displaced
        float t   = float(i) / float(taps - 1);
        vec2  suv = v_uv - radStep * t;                  // walk toward centre
        suv       = clamp(suv, vec2(0.0), vec2(1.0));
        radAcc   += texture(u_tex, suv).rgb;
        radWsum  += 1.0;
    }
    vec3 radResult = radAcc / max(radWsum, 1e-4);

    // ── blend directional ↔ radial ────────────────────────────────────────────
    vec3 blurred = mix(dirResult, radResult, clamp(u_radial, 0.0, 1.0));

    // ── dry/wet ───────────────────────────────────────────────────────────────
    fragColor = vec4(mix(dry, blurred, clamp(u_mix, 0.0, 1.0)), 1.0);
}
