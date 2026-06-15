#version 330 core
// Ported faithfully from v1: chromatic aberration with angular/radial/spin control.
uniform sampler2D u_tex;
uniform vec2      u_resolution;
uniform float     u_time;
uniform float     u_mix;

uniform float u_offset;         // base channel shift
uniform float u_radial;         // radial gain [0..1]
uniform float u_angle;          // aberration angle [0..6.28]
uniform float u_chromatic_spin; // angle spin speed [0..1]

in  vec2 v_uv;
out vec4 out_color;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float angle = u_angle + u_time * u_chromatic_spin * 6.28318530718;
    vec2 dir = vec2(cos(angle), sin(angle)) * u_offset;

    float dist = length(v_uv - 0.5) * 2.0;
    float radialFactor = dist * u_radial + (1.0 - u_radial);
    dir *= radialFactor;

    float r = texture(u_tex, v_uv + dir).r;
    float g = texture(u_tex, v_uv).g;
    float b = texture(u_tex, v_uv - dir).b;
    vec3 wet = vec3(r, g, b);

    out_color = vec4(mix(dry, wet, u_mix), 1.0);
}
