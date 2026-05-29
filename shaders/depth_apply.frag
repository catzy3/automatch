#version 330

// Restore the "saved" snapshot in regions where depth is outside the
// chosen range. Usage in pipeline:
//
//   luma_key
//   depth_save        ← snapshots the frame BEFORE the masked effects
//   datamosh          ← these effects run
//   wave_warp
//   depth_apply       ← reverts the masked area back to the snapshot
//   heavy_grain       ← runs over the whole composite
//
// `depth_apply` reads the depth map and:
//   - depth ∈ [min, max]  → keeps the *processed* pixel (effects visible)
//   - depth outside      → blends back to the snapshot
//
// `feather` softens the band edges.

uniform sampler2D u_tex;       // current (post-effects)
uniform sampler2D u_prev;      // snapshot from depth_save
uniform sampler2D u_depth;     // depth map (R channel = 0 close .. 1 far)
uniform float u_depth_min;     // 0..1
uniform float u_depth_max;     // 0..1
uniform float u_feather;       // 0..0.5
uniform float u_invert;        // 0..1: 0 = keep effects in range, 1 = keep outside
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 cur  = texture(u_tex,  v_uv).rgb;
    vec3 prev = texture(u_prev, v_uv).rgb;
    float d = texture(u_depth, v_uv).r;

    float lo = min(u_depth_min, u_depth_max);
    float hi = max(u_depth_min, u_depth_max);
    float fade = max(u_feather, 0.001);
    float inside = smoothstep(lo - fade, lo + fade, d)
                 * (1.0 - smoothstep(hi - fade, hi + fade, d));
    if (u_invert > 0.5) inside = 1.0 - inside;

    // inside the band: show effects (current)
    // outside: revert to snapshot
    vec3 wet = mix(prev, cur, inside);
    fragColor = vec4(mix(cur, wet, u_mix), 1.0);
}
