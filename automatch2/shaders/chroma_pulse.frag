#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform float u_beat;       // 0..1 beat pulse (REQUIRED by this effect)
uniform vec2  u_resolution;
uniform float u_p0;         // strength
uniform float u_p1;         // ring radius scale

void main() {
    vec3 src = texture(u_tex, v_uv).rgb;

    // aspect-correct vector from centre
    float aspect = u_resolution.x / max(u_resolution.y, 1.0);
    vec2 toC = v_uv - 0.5;
    vec2 toCa = vec2(toC.x * aspect, toC.y);
    float dist = length(toCa);                 // 0 centre .. ~0.7 corner
    vec2 dir = toCa / max(dist, 1e-4);
    vec2 dir_uv = vec2(dir.x / aspect, dir.y); // back to uv space

    // beat envelope: punch on beat, smooth decay between
    float beat = clamp(u_beat, 0.0, 1.0);
    float env = beat * beat;                   // sharper attack

    // expanding ring: front sweeps 0 -> radius as beat decays from 1 -> 0
    float maxR = 0.62 * u_p1;
    float front = (1.0 - beat) * maxR;         // ring travels outward as beat fades
    float ringW = 0.16 * u_p1;
    float ring = exp(-pow((dist - front) / ringW, 2.0)); // gaussian band at the front

    // ---- zoom-punch: suck/snap UV toward centre at the ring, scaled by env ----
    float zoom = ring * env * 0.10 * u_p0;
    vec2 baseUv = v_uv - dir_uv * zoom * dist;

    // ---- radial chromatic aberration concentrated on the ring front ----
    float ca = (ring * env + 0.04 * env) * 0.045 * u_p0 * (0.4 + dist);
    vec2 off = dir_uv * ca;
    float r = texture(u_tex, baseUv + off).r;
    float g = texture(u_tex, baseUv).g;
    float b = texture(u_tex, baseUv - off).b;
    vec3 col = vec3(r, g, b);

    // bright chromatic rim flash riding the ring crest
    vec3 flash = vec3(0.9, 0.55, 1.0) * ring * env * 0.6 * u_p0;
    col += flash;

    // subtle darkening just behind the front for depth, brighten the crest
    col *= 1.0 + ring * env * 0.25 * u_p0;

    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
