#version 330

// Vertical pass + composite. Reads horizontal-bloom buffer, blurs vertically,
// then screen-blends the result onto the original frame.

uniform sampler2D u_tex;       // original frame
uniform sampler2D u_bloom_h;   // horizontally-blurred bloom
uniform vec2  u_resolution;
uniform float u_radius;
uniform float u_intensity;     // 0..3 — bloom strength
uniform vec3  u_tint;          // glow color tint
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    float r = mix(2.0, 40.0, u_radius) / u_resolution.y;

    float wts[7] = float[7](0.196, 0.175, 0.121, 0.066, 0.028, 0.009, 0.002);

    vec3 sum = vec3(0.0);
    float wsum = 0.0;
    for (int i = -6; i <= 6; ++i) {
        vec2 off = vec2(0.0, float(i) * r);
        sum += texture(u_bloom_h, v_uv + off).rgb * wts[abs(i)];
        wsum += wts[abs(i)];
    }
    vec3 bloom = (sum / max(wsum, 1e-4)) * u_intensity;
    bloom *= u_tint;

    // screen blend
    vec3 wet = 1.0 - (1.0 - dry) * (1.0 - bloom);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
