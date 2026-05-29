#version 330

// Composit a precomputed RGBA overlay (CPU-drawn boxes, dots, lines, text)
// over the current frame. Overlay alpha controls the blend; nothing fancy.

uniform sampler2D u_tex;
uniform sampler2D u_overlay;
uniform float u_brightness;   // 0..3 — multiplier on overlay so it can glow
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec4 ov = texture(u_overlay, v_uv);

    vec3 wet = mix(dry, ov.rgb * u_brightness, ov.a);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
