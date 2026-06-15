#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0; // threshold knob
uniform float u_p1; // intensity knob

// luminance bright-pass: keep only what's above the threshold, soft knee
vec3 brightPass(vec3 c, float thr){
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float k = smoothstep(thr, thr + 0.25, l);   // soft knee shoulder
    return c * k;
}

void main(){
    vec3 src = texture(u_tex, v_uv).rgb;

    // map param: default 1.0 -> threshold ~0.62 (visible-but-tasteful)
    float thr = clamp(0.62 * u_p0, 0.0, 1.5);

    // anamorphic radius: much wider horizontally than vertically, gently breathing
    vec2 px = 1.0 / u_resolution;
    float breathe = 1.0 + 0.12 * sin(u_time * 0.6);
    vec2 hStep = vec2(px.x * 3.0 * breathe, 0.0);          // wide horizontal streak
    vec2 vStep = vec2(0.0, px.y * 1.0);                    // tight vertical
    vec2 dStep = vec2(px.x * 1.6, px.y * 0.9);             // slight diagonal body

    // 13-tap gaussian-ish kernel, sampled along stretched axes
    float w[7];
    w[0]=0.196; w[1]=0.175; w[2]=0.121; w[3]=0.066; w[4]=0.028; w[5]=0.009; w[6]=0.002;

    vec3 bloom = brightPass(src, thr) * w[0];
    for(int i = 1; i < 7; i++){
        float fi = float(i);
        // wide horizontal streaks (anamorphic signature)
        bloom += brightPass(texture(u_tex, v_uv + hStep * fi).rgb, thr) * w[i];
        bloom += brightPass(texture(u_tex, v_uv - hStep * fi).rgb, thr) * w[i];
        // soft vertical falloff
        bloom += brightPass(texture(u_tex, v_uv + vStep * fi).rgb, thr) * w[i] * 0.45;
        bloom += brightPass(texture(u_tex, v_uv - vStep * fi).rgb, thr) * w[i] * 0.45;
        // diagonal body fill for a rounder halo
        bloom += brightPass(texture(u_tex, v_uv + dStep * fi).rgb, thr) * w[i] * 0.30;
        bloom += brightPass(texture(u_tex, v_uv - dStep * fi).rgb, thr) * w[i] * 0.30;
    }

    // subtle anamorphic tint: streaks lean cool/blue like a real lens flare
    vec3 tint = vec3(0.85, 0.95, 1.18);
    bloom *= tint;

    // additive glow over the original; default intensity 1.0 -> strength ~1.6
    float strength = 1.6 * u_p1;
    vec3 col = src + bloom * strength;

    // gentle filmic-ish rolloff so highlights bloom instead of clipping hard
    col = col / (1.0 + col * 0.35);
    col *= 1.1225; // exposure compensation for the rolloff

    fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
