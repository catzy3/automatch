#version 330

// Pass 2 of light_bleed: composite the bloom accumulator onto the current frame.
// Screen blend, with optional vertical CCD smear for that telltale Sony-camera streak
// on bright point lights.

uniform sampler2D u_tex;     // current frame
uniform sampler2D u_bloom;   // accumulator from pass 1
uniform float u_smear;       // 0..1 — vertical CCD smear amount
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;
    vec3 bloom = texture(u_bloom, v_uv).rgb;

    // vertical CCD smear: sample the bloom several taps up & down and take max
    if (u_smear > 0.001) {
        float stride = 0.004;
        vec3 streak = bloom;
        for (int i = 1; i <= 8; ++i) {
            float k = float(i) / 8.0;
            float w = (1.0 - k) * u_smear;
            vec3 a = texture(u_bloom, vec2(v_uv.x, v_uv.y + k * 0.25)).rgb * w;
            vec3 b = texture(u_bloom, vec2(v_uv.x, v_uv.y - k * 0.25)).rgb * w;
            streak = max(streak, max(a, b));
        }
        bloom = mix(bloom, streak, u_smear);
    }

    vec3 wet = 1.0 - (1.0 - dry) * (1.0 - bloom);
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
