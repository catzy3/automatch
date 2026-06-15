#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0; // amount
uniform float u_p1; // speed

// cheap hash
float h11(float x){ return fract(sin(x*127.1)*43758.5453); }

void main(){
    vec2 uv = v_uv;
    float amount = u_p0;
    float speed  = u_p1;

    // row index 0 at top of readout (v=1) -> 1 at bottom (v=0)
    float row = 1.0 - uv.y;

    // readout phase: lower rows are sampled "later" -> propagating lag down the frame
    float t = u_time * speed;
    float lag = row * 3.5;                 // how far the readout has progressed at this row
    float phase = t * 2.0 - lag;           // wave travels downward over time

    // jelly wobble: stacked sines give an organic non-uniform skew per scanline
    float wob = sin(phase) * 0.6
              + sin(phase * 2.37 + 1.7) * 0.28
              + sin(phase * 0.61 - 0.9) * 0.34;

    // sub-row jitter so individual scanlines tear slightly (true CMOS noise)
    float line = floor(uv.y * u_resolution.y);
    float jit  = (h11(line + floor(t*8.0)) - 0.5) * 0.12;

    // horizontal skew driven by wobble; scales with row so the bottom smears most
    float skew = (wob + jit) * 0.06 * amount * (0.35 + row);

    // slight vertical lag: each row also pulls content from a neighbouring readout line
    float vlag = cos(phase * 1.3) * 0.012 * amount;

    vec2 suv = vec2(uv.x + skew, uv.y + vlag);

    // wrap horizontally so the skew folds instead of clamping to black bars
    suv.x = fract(suv.x);
    suv.y = clamp(suv.y, 0.0, 1.0);

    // chromatic readout offset — RGB channels latch at micro-different times
    float ca = skew * 0.18 + 0.0015 * amount;
    vec3 col;
    col.r = texture(u_tex, vec2(fract(suv.x + ca), suv.y)).r;
    col.g = texture(u_tex, suv).g;
    col.b = texture(u_tex, vec2(fract(suv.x - ca), suv.y)).b;

    // faint horizontal banding pulse riding the readout wave
    float band = 0.04 * amount * sin(phase * 1.0);
    col *= 1.0 + band;

    vec3 src = texture(u_tex, v_uv).rgb;
    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
