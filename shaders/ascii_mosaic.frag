#version 330
// ─────────────────────────────────────────────────────────────────────────────
//  ascii_mosaic — proper area-averaging with gamma-correct downsampling
//
//  Improvements:
//    1. NINE-TAP area average per cell (3×3 jittered grid) instead of
//       single centre sample.  Massively reduces flicker on moving
//       content and removes the "stair-step" aliasing you'd see on
//       gradients.
//    2. GAMMA-CORRECT averaging.  Straight averaging of sRGB values is
//       wrong (sRGB is non-linear).  We convert to linear, average,
//       convert back.  This gives the right perceptual brightness.
//    3. PERCEPTUAL LUMA (Oklab L*) for glyph index lookup, not linear
//       luma.  Glyph density steps now feel evenly spaced to the eye.
//    4. ATLAS linear filtering for sub-cell anti-aliasing.
// ─────────────────────────────────────────────────────────────────────────────

uniform sampler2D u_tex;
uniform sampler2D u_atlas;
uniform vec2  u_resolution;
uniform float u_cell_size;
uniform float u_glyph_count;
uniform float u_invert;
uniform float u_color_source;
uniform vec3  u_tint;
uniform float u_mix;

in  vec2 v_uv;
out vec4 fragColor;

vec3 srgb_to_lin(vec3 c) { return c * c * (c * 0.305306 + 0.682171) + c * 0.012522; }

// Cheap Oklab L* — see luma_key.frag for full discussion.
float oklab_L(vec3 lin) {
    float l = 0.4122214708*lin.r + 0.5363325363*lin.g + 0.0514459929*lin.b;
    float m = 0.2119034982*lin.r + 0.6806995451*lin.g + 0.1073969566*lin.b;
    float s = 0.0883024619*lin.r + 0.2817188376*lin.g + 0.6299787005*lin.b;
    float l_ = pow(max(l, 0.0), 1.0/3.0);
    float m_ = pow(max(m, 0.0), 1.0/3.0);
    float s_ = pow(max(s, 0.0), 1.0/3.0);
    return 0.2104542553*l_ + 0.7936177850*m_ - 0.0040720468*s_;
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float cellPx = mix(4.0, 32.0, clamp(u_cell_size, 0.0, 1.0));
    cellPx = max(2.0, floor(cellPx));

    vec2 px      = v_uv * u_resolution;
    vec2 cellIdx = floor(px / cellPx);
    vec2 inCell  = px - cellIdx * cellPx;
    vec2 cellLoc = inCell / cellPx;

    // ─── 1) Nine-tap area average over the cell ───
    vec2 base = (cellIdx + 0.5) * cellPx / u_resolution;
    vec2 d    = (cellPx * 0.30) / u_resolution;
    vec3 acc  = vec3(0.0);
    // 3×3 sample grid: corners + edges + centre, all summed in LINEAR space
    acc += srgb_to_lin(texture(u_tex, base + vec2(-d.x, -d.y)).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2( 0.0, -d.y)).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2( d.x, -d.y)).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2(-d.x,  0.0)).rgb);
    acc += srgb_to_lin(texture(u_tex, base                  ).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2( d.x,  0.0)).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2(-d.x,  d.y)).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2( 0.0,  d.y)).rgb);
    acc += srgb_to_lin(texture(u_tex, base + vec2( d.x,  d.y)).rgb);
    vec3 lin  = acc / 9.0;
    vec3 srcCol = sqrt(lin);                   // back to ~sRGB

    // ─── 3) Perceptual L* for glyph index ───
    float L = oklab_L(lin);
    if (u_invert > 0.5) L = 1.0 - L;

    // glyph index by perceptual lightness
    float gc = max(2.0, floor(u_glyph_count));
    int idx = int(clamp(floor(L * gc), 0.0, gc - 1.0));

    // ─── 4) Linear-filtered atlas sample ───
    float ax = (float(idx) + cellLoc.x) / gc;
    float ay = cellLoc.y;
    float glyph = texture(u_atlas, vec2(ax, ay)).r;

    vec3 baseCol = (u_color_source > 0.5) ? srcCol : u_tint;
    vec3 wet     = baseCol * glyph;

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
