#version 330

// Horizontal pass of a 2-pass separable bloom. Outputs ONLY the bloom
// contribution (bright pixels, blurred horizontally), not the composited
// image. The vertical pass blurs further and composites onto the original.

uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_threshold;   // luma threshold for bloom seed
uniform float u_radius;      // 0..1 → ~2..40 px

in vec2 v_uv;
out vec4 fragColor;

void main() {
    float r = mix(2.0, 40.0, u_radius) / u_resolution.x;

    // 13-tap Gaussian
    float wts[7] = float[7](0.196, 0.175, 0.121, 0.066, 0.028, 0.009, 0.002);

    vec3 sum = vec3(0.0);
    float wsum = 0.0;
    for (int i = -6; i <= 6; ++i) {
        vec2 off = vec2(float(i) * r, 0.0);
        vec3 c = texture(u_tex, v_uv + off).rgb;
        // soft tonemap before extraction so a bright pixel doesn't dominate
        // and turn the bloom uniformly white
        c = c / (1.0 + c);
        float y = dot(c, vec3(0.299, 0.587, 0.114));
        // smooth threshold curve, not a hard cutoff
        float bright = smoothstep(u_threshold, u_threshold + 0.15, y);
        vec3 seed = c * bright;
        float w = wts[abs(i)];
        sum += seed * w;
        wsum += w;
    }
    fragColor = vec4(sum / max(wsum, 1e-4), 1.0);
}
