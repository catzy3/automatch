#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0;   // depth: number of mirrored tunnel rings packed per turn
uniform float u_p1;   // spin: rotation + travel speed

#define TAU 6.28318530718

// triangle wave for seamless mirroring (ping-pong 0..1..0)
float mirror1(float x){ return abs(fract(x*0.5)*2.0 - 1.0); }

void main(){
    vec3 src = texture(u_tex, v_uv).rgb;

    // centred, aspect-corrected coords
    vec2 p = v_uv - 0.5;
    p.x *= u_resolution.x / max(u_resolution.y, 1.0);

    float r   = length(p) + 1e-4;
    float ang = atan(p.y, p.x);

    // log-polar: equal screen-space rings map to equal log-radius bands,
    // giving the self-similar "infinite tunnel" recursion.
    float depth = max(u_p0, 0.05) * 4.0;
    float lr = log(r) * depth;

    // travel into the tunnel + slow rotation, driven by spin
    float t = u_time;
    lr  += t * 0.65 * sign(u_p1 + 0.0001) * (0.4 + abs(u_p1));
    ang += t * 0.25 * u_p1;

    // mirror the radial bands so the image flips each ring -> kaleidoscopic depth
    float ring = mirror1(lr);

    // also mirror the angle into a wedge so panels tile seamlessly
    float wedge = mirror1(ang / TAU * 8.0);

    // reconstruct a sampling uv from the tunnelled coordinates.
    // ring drives a radial gradient back into the source, wedge sweeps across it.
    vec2 tuv;
    tuv.x = wedge;
    tuv.y = ring;

    // subtle swirl so straight panels become a spiraling vortex
    float sw = (ring - 0.5) * 1.4 * u_p1;
    tuv = vec2(0.5) + mat2(cos(sw), -sin(sw), sin(sw), cos(sw)) * (tuv - 0.5);

    // depth fog: rings near the centre darken to sell the infinite recession
    float fog = smoothstep(0.0, 0.85, ring);
    float vign = 1.0 - smoothstep(0.35, 0.72, r);

    vec3 col = texture(u_tex, fract(tuv)).rgb;

    // chromatic depth bleed between successive rings for that viral shimmer
    float sh = 0.012 * (1.0 + abs(u_p1));
    col.r = texture(u_tex, fract(tuv + vec2( sh, 0.0))).r;
    col.b = texture(u_tex, fract(tuv - vec2( sh, 0.0))).b;

    col *= mix(0.35, 1.15, fog);
    col *= mix(0.5, 1.0, vign);

    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
