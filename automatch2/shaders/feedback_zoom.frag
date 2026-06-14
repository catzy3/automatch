#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;   // current frame
uniform sampler2D u_prev;  // previous chain output (feedback)
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// Feedback zoom — the previous frame is scaled+rotated slightly and fed back,
// creating an infinite Droste-style tunnel of trailing motion.
void main() {
    vec3 cur = texture(u_tex, v_uv).rgb;
    vec2 c = v_uv - 0.5;
    float ang = 0.02 * sin(u_time * 0.3);
    float s = sin(ang), co = cos(ang);
    c = mat2(co, -s, s, co) * c * 0.985; // rotate + zoom in
    vec3 prev = texture(u_prev, clamp(c + 0.5, 0.0, 1.0)).rgb * 0.95;
    vec3 col = max(cur, prev);
    fragColor = vec4(mix(cur, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
