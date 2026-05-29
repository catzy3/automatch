#version 330

// Mirror flow: pixels stream downward, and as they age (move down) the previous
// content gets progressively mirrored. Near the top: untouched; near the bottom:
// fully flipped horizontally and/or vertically. Accumulating through ping-pong
// gives a "liquid unroll" — the longer the camera sits still, the more the
// stream lower in frame inverts the image.
//
// Knobs:
//   u_drip      — vertical streaming speed
//   u_decay     — how fast the trail fades
//   u_unroll    — overall mirror strength (0 = no flip, 1 = full)
//   u_axis_h    — horizontal flip weight (0..1)
//   u_axis_v    — vertical flip weight   (0..1)
//   u_swirl     — adds a slight per-pixel angular twist while flipping
//   u_chaos     — randomizes the unroll boundary so the flip line is jagged
//   u_mix       — wet/dry

uniform sampler2D u_current;
uniform sampler2D u_prev;
uniform float u_time;
uniform float u_drip;
uniform float u_decay;
uniform float u_unroll;
uniform float u_axis_h;
uniform float u_axis_v;
uniform float u_swirl;
uniform float u_chaos;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec3 dry = texture(u_current, v_uv).rgb;

    // base sampling point: above us → looks like content sliding down
    vec2 sampUV = vec2(v_uv.x, v_uv.y + u_drip);

    // age: how far down we are. 0 at top, 1 at bottom.
    float age = clamp(1.0 - v_uv.y, 0.0, 1.0);

    // jaggy unroll boundary
    float jag = (hash21(floor(v_uv * vec2(160.0, 60.0)) + floor(u_time * 4.0)) - 0.5)
                * 0.25 * u_chaos;
    float t = clamp(age * u_unroll + jag, 0.0, 1.0);

    // smooth flip on each axis, independently weighted
    float fX = smoothstep(0.25, 0.85, t * u_axis_h);
    float fY = smoothstep(0.25, 0.85, t * u_axis_v);

    vec2 flippedUV = vec2(
        mix(sampUV.x, 1.0 - sampUV.x, fX),
        mix(sampUV.y, 1.0 - sampUV.y, fY)
    );

    // slight rotation while in transit (looks like pixel is tumbling)
    if (u_swirl > 0.001) {
        vec2 c = flippedUV - 0.5;
        float ang = (t - 0.5) * u_swirl * 1.2 + 0.02 * sin(u_time + flippedUV.y * 6.0);
        float ca = cos(ang); float sa = sin(ang);
        flippedUV = vec2(c.x*ca - c.y*sa, c.x*sa + c.y*ca) + 0.5;
    }

    vec3 prevA = texture(u_prev, flippedUV).rgb;
    vec3 prevB = texture(u_prev, flippedUV + vec2(0.0,  u_drip * 0.5)).rgb;
    vec3 prevC = texture(u_prev, flippedUV + vec2(0.0, -u_drip * 0.5)).rgb;
    vec3 prev = (prevA * 2.0 + prevB + prevC) * 0.25 * u_decay;

    // screen blend so trails are luminous on dark areas
    vec3 wet = 1.0 - (1.0 - dry) * (1.0 - prev);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
