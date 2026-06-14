#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;            // current frame
uniform sampler2DArray u_history;   // ring of recent input frames
uniform int u_history_n;            // ring size
uniform int u_history_head;         // next write slot (most-recent = head-1)
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;

// Time displacement / time-slice: each scanline shows a progressively older
// frame, so motion smears across time — the "rainbow streak" look.
void main() {
    vec3 cur = texture(u_tex, v_uv).rgb;
    int n = max(u_history_n, 1);
    int most_recent = (u_history_head - 1 + n) % n;
    int delay = int(v_uv.y * float(n - 1));
    int layer = (most_recent - delay + n * n) % n;
    vec3 col = texture(u_history, vec3(v_uv, float(layer))).rgb;
    fragColor = vec4(mix(cur, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
