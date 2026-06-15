#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  iridescent_foil — angle-dependent hue shift + metallic specular
//
//  Simulates a holographic foil sticker laid over the source frame:
//    1. LUMA GRADIENT → fake surface normal from central-difference
//       derivatives of the frame's perceived brightness.  The gradient
//       gives every pixel a local "tilt" without geometry data.
//    2. FAKE FRESNEL: dot(fake_normal, view_dir) produces a scalar that
//       drives hue rotation.  Flat areas near the camera axis have low
//       dot values; "edge" regions (high-gradient) have high dot values.
//    3. HUE FIELD: the dot scalar is combined with screen-space UV and
//       u_time to create a slowly-drifting iridescent colour field.
//    4. METALLIC SPECULAR: a tight power curve on the same dot scalar
//       produces the sharp bright highlights characteristic of foil.
//    5. COMPOSITING: the foil colour is blended multiplicatively+boost
//       over the original frame, then the whole effect is faded with
//       u_opacity and finally mixed dry/wet via u_mix.
//
//  Uniform ranges
//    u_hue_range  0.0 … 1.0    — fraction of hue wheel swept (0.3 = 108°)
//    u_speed      0.0 … 2.0    — drift speed in hue-turns / second
//    u_metallic   0.0 … 1.0    — specular highlight intensity
//    u_scale      0.5 … 4.0    — spatial frequency of the foil pattern
//    u_opacity    0.0 … 1.0    — foil overlay opacity
//    u_mix        0.0 … 1.0    — dry / wet
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;
uniform float u_hue_range;
uniform float u_speed;
uniform float u_metallic;
uniform float u_scale;
uniform float u_opacity;

in  vec2 v_uv;
out vec4 fragColor;

// ─────────────────────────────────────────────────────────────────────────────
//  Colour utilities
// ─────────────────────────────────────────────────────────────────────────────

// HSV → RGB  (H is in [0,1], wraps automatically)
vec3 hsv2rgb(vec3 c) {
    // Standard 6-segment hue conversion
    vec3 p = abs(fract(c.xxx + vec3(1.0, 2.0/3.0, 1.0/3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

// Perceptual luma (Rec. 709)
float luma(vec3 c) {
    return dot(c, vec3(0.2126, 0.7152, 0.0722));
}

// ─────────────────────────────────────────────────────────────────────────────
//  Luma-gradient normal estimation
//
//  Central differences on luma over one texel in each axis give us a
//  rough approximation of the local surface gradient.  We embed this
//  2-D gradient as the XY components of a fake surface normal and
//  derive Z so the vector has unit length.  This keeps everything on
//  the hemisphere facing the camera.
//
//  texel_offset: one-pixel step in UV space
// ─────────────────────────────────────────────────────────────────────────────
vec3 fake_normal(vec2 uv, vec2 texel) {
    float l_r = luma(texture(u_tex, uv + vec2( texel.x, 0.0)).rgb);
    float l_l = luma(texture(u_tex, uv + vec2(-texel.x, 0.0)).rgb);
    float l_u = luma(texture(u_tex, uv + vec2(0.0,  texel.y)).rgb);
    float l_d = luma(texture(u_tex, uv + vec2(0.0, -texel.y)).rgb);

    // Central-difference gradient (scaled up so subtle edges are visible)
    float gx = (l_r - l_l) * 4.0;
    float gy = (l_u - l_d) * 4.0;

    // Reconstruct Z to keep normal on the unit hemisphere
    float gz = sqrt(max(1.0 - gx*gx - gy*gy, 0.0));
    return normalize(vec3(gx, gy, gz));
}

// ─────────────────────────────────────────────────────────────────────────────
//  Main
// ─────────────────────────────────────────────────────────────────────────────
void main() {
    vec3 orig = texture(u_tex, v_uv).rgb;

    vec2 texel = 1.0 / max(u_resolution, vec2(1.0));  // guard against zero res

    // ── 1. Fake surface normal from luma gradient ──
    vec3  N        = fake_normal(v_uv, texel);
    vec3  view_dir = vec3(0.0, 0.0, 1.0);             // fixed camera looking down Z
    float dot_nv   = clamp(dot(N, view_dir), 0.0, 1.0);

    // ── 2. Hue field ──────────────────────────────────────────────────────────
    //  Compose a smoothly varying hue from:
    //    • dot_nv        — local surface angle (main iridescent driver)
    //    • uv.x * scale  — horizontal spatial frequency
    //    • time * speed  — temporal drift
    //  fract() keeps the hue inside [0,1] and makes it wrap without a
    //  hard seam (since hsv2rgb already handles the periodic mapping).
    float hue = fract(
        dot_nv    * u_hue_range
        + v_uv.x  * u_scale * 0.3     // gentle horizontal sweep
        + v_uv.y  * u_scale * 0.1     // subtle vertical offset
        + u_time  * u_speed * 0.05    // slow temporal drift
    );

    // Full saturation, moderate brightness — multiply over source later
    vec3 foil_rgb = hsv2rgb(vec3(hue, 1.0, 1.0));

    // ── 3. Metallic specular highlight ────────────────────────────────────────
    //  A tight power curve emphasises only the highest dot values,
    //  creating the bright "hot spot" flare typical of real foil.
    //  Exponent 12 gives a moderately tight lobe; scale by u_metallic.
    float spec     = pow(dot_nv, 12.0) * u_metallic * 3.0;
    foil_rgb      += vec3(spec);           // additive white specular

    // ── 4. Composite foil over original ──────────────────────────────────────
    //  Multiplicative blend with a small brightness boost (1.4) keeps the
    //  source image readable while adding the colour shift.
    //  u_opacity controls how aggressively the foil takes over.
    vec3 foil_over = mix(orig, orig * foil_rgb * 1.4, u_opacity);

    // Clamp after the multiplicative step — values can exceed 1.0
    foil_over = clamp(foil_over, 0.0, 1.0);

    // ── 5. Dry / wet ──────────────────────────────────────────────────────────
    fragColor = vec4(mix(orig, foil_over, u_mix), 1.0);
}
