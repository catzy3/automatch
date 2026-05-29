#version 330
// Slit-scan — temporal "waterfall" time-smear.
// Each frame the accumulator (previous output) scrolls by `scroll`, and a single
// capture line of the LIVE frame (at u_scanpos) is injected into the vacated
// edge.  Over time every output line shows a different moment → classic slit
// scan.  u_direction picks vertical (rows) or horizontal (columns) scroll.
//
// Bound like motion_trails: u_current = slot 0 (live), u_prev = slot 1 (accum).
uniform sampler2D u_current;
uniform sampler2D u_prev;
uniform float u_time;
uniform float u_mix;
uniform float u_direction;   // 0 = vertical, 1 = horizontal
uniform float u_speed;       // scroll speed (0..1 → up to ~4% of frame / frame)
uniform float u_scanpos;     // capture line position along the scroll axis (0..1)
uniform float u_feedback;    // persistence of old lines (1 = none lost)
uniform float u_threshold;   // luma gate on the captured line (0 = off, >0 = cut)
uniform vec2  u_resolution;

in vec2 v_uv;
out vec4 fragColor;

// Luma gate baked into the freshly captured scan line.  Below `u_threshold`
// the pixel is crushed toward black, above it the colour is kept (and slightly
// boosted) → stark high-contrast slit-scan streaks.  Off when threshold ~ 0.
vec3 _gate(vec3 c) {
    if (u_threshold <= 0.001) return c;
    float luma = dot(c, vec3(0.299, 0.587, 0.114));
    // soft edge ~5% wide around the threshold to avoid hard aliasing
    float mask = smoothstep(u_threshold - 0.05, u_threshold + 0.05, luma);
    return c * mask;
}

void main() {
    vec3 dry = texture(u_current, v_uv).rgb;
    bool horiz = u_direction > 0.5;

    float texel  = horiz ? (1.0 / max(u_resolution.x, 1.0))
                         : (1.0 / max(u_resolution.y, 1.0));
    float scroll = max(texel, u_speed * 0.04);
    float fb     = clamp(u_feedback, 0.0, 1.0);

    vec3 wet;
    if (horiz) {
        if (v_uv.x < scroll) {
            // newest: live column sampled at the capture position
            wet = _gate(texture(u_current, vec2(u_scanpos, v_uv.y)).rgb);
        } else {
            // older content drifts right
            wet = texture(u_prev, vec2(v_uv.x - scroll, v_uv.y)).rgb * fb;
        }
    } else {
        if (v_uv.y > 1.0 - scroll) {
            // newest: live row sampled at the capture position
            wet = _gate(texture(u_current, vec2(v_uv.x, u_scanpos)).rgb);
        } else {
            // older content drifts down
            wet = texture(u_prev, vec2(v_uv.x, v_uv.y + scroll)).rgb * fb;
        }
    }

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
