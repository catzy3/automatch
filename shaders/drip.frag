#version 330

uniform sampler2D u_current;
uniform sampler2D u_prev;
uniform float u_time;
uniform float u_drip;
uniform float u_decay;
uniform float u_mix;

in vec2 v_uv;
out vec4 fragColor;

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec3 dry = texture(u_current, v_uv).rgb;

    // sample prev from a position above us → looks like content sliding down
    float jitter = (hash21(vec2(floor(v_uv.y * 240.0), floor(u_time * 30.0))) - 0.5) * 0.0015;
    vec2 prevUV = vec2(v_uv.x + jitter, v_uv.y + u_drip);

    vec3 prevA = texture(u_prev, prevUV).rgb;
    vec3 prevB = texture(u_prev, prevUV + vec2(0.0,  u_drip * 0.5)).rgb;
    vec3 prevC = texture(u_prev, prevUV + vec2(0.0, -u_drip * 0.5)).rgb;
    vec3 prev = (prevA * 2.0 + prevB + prevC) * 0.25 * u_decay;

    // screen blend
    vec3 trail = prev;
    vec3 wet = 1.0 - (1.0 - dry) * (1.0 - trail);

    fragColor = vec4(mix(dry, wet, u_mix), 1.0);
}
