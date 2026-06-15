#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;            // current frame
uniform sampler2D u_prev;           // previous chain output (feedback)
uniform sampler2DArray u_history;   // ring of recent input frames
uniform int u_history_n;
uniform int u_history_head;
uniform float u_mix;
uniform float u_time;
uniform vec2 u_resolution;
uniform float u_p0; // flow strength

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

// Real-time optical-flow datamosh: estimate per-pixel motion (single-pixel
// Lucas-Kanade from the temporal + spatial gradient between the current and
// previous input), then drag the previous OUTPUT along that motion so old
// content "melts" forward — reseeded slightly so it doesn't dissolve.
void main() {
    vec2 px = 1.0 / max(u_resolution, vec2(1.0));
    vec3 cur = texture(u_tex, v_uv).rgb;

    int n = max(u_history_n, 1);
    int prev_layer = (u_history_head - 2 + n * n) % n; // previous input frame
    vec3 prev_in = texture(u_history, vec3(v_uv, float(prev_layer))).rgb;

    float it = luma(cur) - luma(prev_in);
    float gx = luma(texture(u_tex, v_uv + vec2(px.x, 0.0)).rgb)
             - luma(texture(u_tex, v_uv - vec2(px.x, 0.0)).rgb);
    float gy = luma(texture(u_tex, v_uv + vec2(0.0, px.y)).rgb)
             - luma(texture(u_tex, v_uv - vec2(0.0, px.y)).rgb);
    vec2 grad = vec2(gx, gy);
    vec2 flow = -it * grad / (dot(grad, grad) + 0.001) * u_p0;
    flow = clamp(flow, vec2(-0.05), vec2(0.05)); // keep it from running away

    vec3 moshed = texture(u_prev, clamp(v_uv - flow, 0.0, 1.0)).rgb;
    moshed = mix(moshed, cur, 0.06); // gentle reseed

    fragColor = vec4(mix(cur, moshed, clamp(u_mix, 0.0, 1.0)), 1.0);
}
