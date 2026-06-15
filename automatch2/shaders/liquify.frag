#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0; // amount
uniform float u_p1; // scale

// --- inline value noise ---
float hash21(vec2 p){
    p = fract(p * vec2(123.34, 345.45));
    p += dot(p, p + 34.345);
    return fract(p.x * p.y);
}
float vnoise(vec2 p){
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash21(i + vec2(0.0, 0.0));
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
// fractal value noise -> scalar potential field
float fbm(vec2 p){
    float s = 0.0, a = 0.5;
    for(int i = 0; i < 4; i++){
        s += a * vnoise(p);
        p = p * 2.02 + vec2(11.7, 4.3);
        a *= 0.5;
    }
    return s;
}
// curl of the scalar field (rotate gradient 90deg) -> divergence-free flow
vec2 curl(vec2 p){
    float e = 0.035;
    float dx = fbm(p + vec2(e, 0.0)) - fbm(p - vec2(e, 0.0));
    float dy = fbm(p + vec2(0.0, e)) - fbm(p - vec2(0.0, e));
    return vec2(dy, -dx) / (2.0 * e);
}

void main(){
    vec2 uv = v_uv;
    float scale = 3.0 * u_p1;
    float t = u_time * 0.35;
    // drifting sample positions so the field churns and melts
    vec2 q = uv * scale + vec2(0.0, t * 0.8);
    // two-stage domain warp: flow advects, then re-flow for swirling melt
    vec2 flow = curl(q + 0.6 * t);
    vec2 flow2 = curl(q * 1.7 + flow * 1.3 - vec2(0.0, t));
    vec2 warp = (flow * 0.7 + flow2 * 0.45);
    // downward gravity bias so it drips like melting liquid
    warp.y -= 0.35 * fbm(q * 0.8 + t);
    float amt = 0.06 * u_p0;
    vec2 suv = clamp(uv + warp * amt, 0.0, 1.0);
    vec3 col = texture(u_tex, suv).rgb;
    // subtle wet sheen from flow magnitude for that liquid pop
    float sheen = clamp(length(warp) * 0.5, 0.0, 1.0);
    col += sheen * 0.12 * vec3(0.8, 0.95, 1.0);
    vec3 src = texture(u_tex, v_uv).rgb;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
