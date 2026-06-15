#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform float u_beat;   // 0..1 pulse, 1 on the beat
uniform vec2 u_resolution;
uniform float u_p0; // zoom-kick amount

// Beat punch: zoom-in kick + brightness flash + RGB shake, driven by u_beat.
void main() {
    vec3 src = texture(u_tex, v_uv).rgb;
    vec2 c = v_uv - 0.5;
    float z = 1.0 - 0.12 * u_beat * u_p0;      // zoom in on the beat
    vec2 uv = c * z + 0.5;
    float o = 0.005 * u_beat;                   // rgb shake
    float flash = 1.0 + 0.6 * u_beat;
    vec3 col;
    col.r = texture(u_tex, clamp(uv + vec2(o, 0.0), 0.0, 1.0)).r;
    col.g = texture(u_tex, uv).g;
    col.b = texture(u_tex, clamp(uv - vec2(o, 0.0), 0.0, 1.0)).b;
    col *= flash;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
