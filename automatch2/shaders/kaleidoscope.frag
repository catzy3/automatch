#version 330
// Kaleidoscope — polar wedge fold + mirror.
// Spatial-only effect: folds the frame into `u_sides` mirrored segments around
// the centre, with optional static rotation (u_angle) and auto-spin (u_spin).
uniform sampler2D u_tex;
uniform float u_time;
uniform float u_mix;
uniform float u_sides;    // number of mirror segments (2..16)
uniform float u_angle;    // static rotation offset (turns, 0..1)
uniform float u_spin;     // auto-rotation speed (turns/sec, signed)
uniform float u_zoom;     // radial zoom (0.3..3, >1 = zoom in)
uniform vec2  u_resolution;

in vec2 v_uv;
out vec4 fragColor;

const float TAU = 6.28318530718;

void main() {
    vec3 dry = texture(u_tex, v_uv).rgb;

    // aspect-correct, centre-origin coords
    float aspect = u_resolution.x / max(u_resolution.y, 1.0);
    vec2 p = v_uv - 0.5;
    p.x *= aspect;

    float r = length(p);
    float a = atan(p.y, p.x);

    // rotate (static + animated)
    a += (u_angle + u_time * u_spin) * TAU;

    // fold the angle into one mirrored wedge
    float sides = max(2.0, floor(u_sides + 0.5));
    float seg   = TAU / sides;
    a = mod(a, seg);
    a = abs(a - seg * 0.5);          // mirror within the wedge

    // back to cartesian with radial zoom
    float zoom = max(0.05, u_zoom);
    vec2 q = vec2(cos(a), sin(a)) * (r / zoom);
    q.x /= aspect;
    vec2 uv = q + 0.5;

    // mirror-repeat into [0,1] so the wedge tiles seamlessly
    uv = abs(fract(uv * 0.5) * 2.0 - 1.0);

    vec3 wet = texture(u_tex, uv).rgb;
    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
