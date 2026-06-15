#version 330 core
// Ported faithfully from v1: radial swirl / twist warp with smooth falloff.
uniform sampler2D u_tex;
uniform vec2  u_resolution;
uniform float u_time;
uniform float u_mix;

uniform float u_strength;   // peak rotation (radians)
uniform float u_radius;     // effect radius (fraction of min(w,h)/2)
uniform float u_cx;         // centre X
uniform float u_cy;         // centre Y
uniform float u_animate;    // animate strength with sin(u_time)

in  vec2 v_uv;
out vec4 out_color;

vec2 rotate2d(vec2 p, float angle) {
    float s = sin(angle);
    float c = cos(angle);
    return vec2(p.x * c - p.y * s, p.x * s + p.y * c);
}

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    float aspect = u_resolution.x / max(u_resolution.y, 1.0);
    vec2  centre = vec2(clamp(u_cx, 0.0, 1.0), clamp(u_cy, 0.0, 1.0));
    vec2  delta  = v_uv - centre;
    delta.x     *= aspect;

    float radius = max(u_radius, 0.001);
    float dist   = length(delta) / radius;

    float strength;
    if (u_animate > 0.5) {
        strength = u_strength * sin(u_time);
    } else if (u_animate > 0.001) {
        float anim = u_strength * sin(u_time);
        strength   = mix(u_strength, anim, u_animate);
    } else {
        strength = u_strength;
    }

    float falloff = smoothstep(1.0, 0.0, dist);
    float twist   = strength * falloff;

    vec2 warped_delta = rotate2d(delta, twist);
    warped_delta.x   /= aspect;
    vec2 warped_uv = clamp(centre + warped_delta, 0.0, 1.0);

    vec3 wet = texture(u_tex, warped_uv).rgb;
    out_color = vec4(mix(dry, wet, u_mix), 1.0);
}
