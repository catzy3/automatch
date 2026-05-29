#version 330

// Sobel edge detection. Edges are drawn in the accent color over a dimmed
// version of the source. Threshold + thickness controls let you go from
// "subtle outline" to "vectorized silhouette".

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform vec3  u_accent;
uniform float u_threshold;   // 0..1 — edge cutoff
uniform float u_thickness;   // 0..1 — visual thickness multiplier
uniform float u_fade_image;  // 0..1 — how much to dim the source under edges
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float luma(vec2 uv) {
    return dot(texture(u_tex, uv).rgb, vec3(0.299, 0.587, 0.114));
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec2 px = 1.0 / u_resolution;

    float tl = luma(v_uv + px * vec2(-1, -1));
    float tm = luma(v_uv + px * vec2( 0, -1));
    float tr = luma(v_uv + px * vec2( 1, -1));
    float ml = luma(v_uv + px * vec2(-1,  0));
    float mr = luma(v_uv + px * vec2( 1,  0));
    float bl = luma(v_uv + px * vec2(-1,  1));
    float bm = luma(v_uv + px * vec2( 0,  1));
    float br = luma(v_uv + px * vec2( 1,  1));

    float gx = (tr + 2.0*mr + br) - (tl + 2.0*ml + bl);
    float gy = (bl + 2.0*bm + br) - (tl + 2.0*tm + tr);
    float g  = sqrt(gx*gx + gy*gy);

    float thr = mix(0.6, 0.05, u_threshold);
    float edge = smoothstep(thr, thr * (0.5 + 0.5 * u_thickness), g);

    vec3 base = mix(dry, dry * 0.25, u_fade_image);
    vec3 wet = mix(base, u_accent, edge);

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
