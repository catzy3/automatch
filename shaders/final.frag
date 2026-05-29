#version 330

// Final pass: gain/contrast/saturation + outputs to screen/Spout.
//
// Optionally masks everything outside u_mask_bbox to pure black.  Used
// by Chaos aspect mode so the dark canvas around the breathing frame
// stays a true black even after analog effects (grain / VHS / CRT) have
// run — those effects would otherwise lay noise on the void.

uniform sampler2D u_tex;
uniform float u_gain;
uniform float u_contrast;
uniform float u_saturation;
uniform int   u_flip_y;        // 1 → flip vertically for Spout
uniform int   u_use_mask;      // 1 → apply mask
uniform vec4  u_mask_bbox;     // (x0, y0, x1, y1) in [0..1] UV space

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec2 uv = v_uv;
    if (u_flip_y == 1) uv.y = 1.0 - uv.y;

    vec3 col = texture(u_tex, uv).rgb * u_gain;
    col = (col - 0.5) * u_contrast + 0.5;

    float gray = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(gray), col, u_saturation);

    // ── Mask: hard black outside the chaos bbox (UV space) ──
    if (u_use_mask == 1) {
        bool inside = (uv.x >= u_mask_bbox.x && uv.x <= u_mask_bbox.z
                    && uv.y >= u_mask_bbox.y && uv.y <= u_mask_bbox.w);
        if (!inside) col = vec3(0.0);
    }

    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
