#version 330

// Fragment layers — random horizontal slices of the PREVIOUS frame are
// pasted at a different vertical position in the current frame. Looks like
// torn paper strips from earlier moments overlaid on the present.
//
// Each "slice" has independent height, source offset and destination offset
// chosen from per-slice hash + slow time. They cross-fade in/out so it's
// alive without being seizure-inducing.

uniform sampler2D u_tex;     // current
uniform sampler2D u_prev;    // previous datamosh-style memory
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_count;       // 0..1 → 1..12 slices visible at once
uniform float u_height;      // 0..1 → 0.02..0.25 of frame each
uniform float u_offset;      // 0..1 → 0..0.5 max source/dest separation
uniform float u_alpha;       // 0..1 — slice opacity
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec3 wet = dry;

    int N = int(mix(1.0, 12.0, clamp(u_count, 0.0, 1.0)));
    for (int i = 0; i < 12; ++i) {
        if (i >= N) break;
        float seed = float(i) + floor(u_time * 0.5);
        float h  = mix(0.02, 0.25, u_height) * (0.4 + hash11(seed * 3.7));
        float yC = hash11(seed * 1.1);                  // destination center
        float ySrc = mix(yC - u_offset, yC + u_offset,  // source center
                         hash11(seed * 2.7));
        float lifeT = fract(u_time * 0.3 + hash11(seed) * 5.0);
        float fade = smoothstep(0.0, 0.15, lifeT) *
                     (1.0 - smoothstep(0.85, 1.0, lifeT));

        float d = abs(v_uv.y - yC);
        if (d < h * 0.5) {
            // soft edge
            float edge = 1.0 - smoothstep(h * 0.35, h * 0.5, d);
            // optional x-offset per slice
            float xoff = (hash11(seed * 4.7) - 0.5) * u_offset;
            vec2 srcUV = vec2(v_uv.x + xoff, ySrc + (v_uv.y - yC));
            srcUV = clamp(srcUV, vec2(0.0), vec2(1.0));
            vec3 sliceCol = texture(u_prev, srcUV).rgb;
            float a = edge * u_alpha * fade;
            wet = mix(wet, sliceCol, a);
        }
    }
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
