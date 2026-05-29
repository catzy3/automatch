#version 330 core
// -----------------------------------------------------------------------
// warp_composite.frag
// Warps two source frames (current + previous) with independent affine
// matrices, then blends them with a smooth-stepped cross-fade.
//
// Matrices are in "pixel-UV" space: u = x/w, v = y/h (v increases DOWN).
// The shader internally flips v_uv (which increases UP in GL convention)
// to pixel-UV, applies the warp, then flips back before sampling.
//
// Upload matrices column-major: M.T.astype("f4").tobytes()
// -----------------------------------------------------------------------

uniform sampler2D u_cur;        // current  clip frame (RGB)
uniform sampler2D u_prev;       // previous clip frame (RGB)
uniform float     u_fade;       // 0.0 = full prev, 1.0 = full cur
uniform mat3      u_warp_cur;   // pixel-UV backward warp for current  frame
uniform mat3      u_warp_prev;  // pixel-UV backward warp for previous frame

in  vec2 v_uv;
out vec4 out_color;

// Convert a GL UV coordinate (v↑) into a sampled texture colour, applying
// the given pixel-UV backward warp matrix.
vec4 warp_sample(sampler2D tex, mat3 M, vec2 uv_gl) {
    // GL UV → pixel UV  (flip v so it increases downward, matching image y)
    vec2 uv_pix = vec2(uv_gl.x, 1.0 - uv_gl.y);
    // apply backward warp
    vec3 src    = M * vec3(uv_pix, 1.0);
    // pixel UV → GL UV
    vec2 src_gl = vec2(src.x, 1.0 - src.y);
    // clamp (border replicate)
    src_gl = clamp(src_gl, 0.001, 0.999);
    return texture(tex, src_gl);
}

void main() {
    vec4 cur_col  = warp_sample(u_cur,  u_warp_cur,  v_uv);
    vec4 prev_col = warp_sample(u_prev, u_warp_prev, v_uv);
    // smoothstep ease on the fade
    float a = u_fade * u_fade * (3.0 - 2.0 * u_fade);
    out_color   = mix(prev_col, cur_col, a);
}
