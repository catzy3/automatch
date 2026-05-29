#version 330

uniform sampler2D u_tex;
uniform float u_time;
uniform float u_shift;
uniform float u_wave_freq;
uniform float u_wave_speed;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    vec2 center = v_uv - 0.5;
    float radial = length(center);
    float radialAmp = u_shift * 0.03 * radial * radial;
    vec2 dir = normalize(center + 1e-6);

    float wave = sin(v_uv.y * u_wave_freq + u_time * u_wave_speed) * 0.25;
    float waveAmp = u_shift * 0.006 * wave;

    vec2 offsetR =  dir * radialAmp + vec2(waveAmp, 0.0);
    vec2 offsetB = -dir * radialAmp - vec2(waveAmp, 0.0);

    float r = texture(u_tex, v_uv + offsetR).r;
    float g = texture(u_tex, v_uv).g;
    float b = texture(u_tex, v_uv + offsetB).b;
    vec3 wet = vec3(r, g, b);

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
