#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0;   // cell size
uniform float u_p1;   // contrast

float luma(vec3 c){ return dot(c, vec3(0.299,0.587,0.114)); }

// SDF-ish glyph coverage for a normalized cell coord g in [0,1]^2.
// idx selects a ramp glyph: 0 space .. 6 solid block. Returns 1 = ink.
float glyph(int idx, vec2 g){
  vec2 p = g - 0.5;              // center
  float d = max(abs(p.x), abs(p.y));
  if(idx<=0) return 0.0;                                   // ' '  empty
  if(idx==1) return step(length(p), 0.10);                 // '.'  dot
  if(idx==2){ return max(step(length(p-vec2(0.0,0.18)),0.09),
                         step(length(p+vec2(0.0,0.18)),0.09)); } // ':' two dots
  if(idx==3){ float cross=max(step(abs(p.x),0.10),step(abs(p.y),0.10));
              return cross*step(d,0.40); }                 // '+'  plus
  if(idx==4){ float a=step(abs(abs(p.x)-abs(p.y)),0.12);   // 'x/*' star
              float b=max(step(abs(p.x),0.09),step(abs(p.y),0.09));
              return max(a,b)*step(d,0.42); }
  if(idx==5){ vec2 q=fract(g*4.0);                          // '#'  hash grid
              float gr=max(step(abs(q.x-0.5),0.18),step(abs(q.y-0.5),0.18));
              return gr*step(d,0.46); }
  return step(d,0.46);                                     // '@'  block
}

void main(){
  vec2 src_uv = v_uv;
  vec3 src = texture(u_tex, src_uv).rgb;

  // cell grid in pixels (square cells), scaled by param
  float cell = clamp(8.0*u_p0, 3.0, 64.0);
  vec2 px = v_uv * u_resolution;
  vec2 cellId = floor(px / cell);
  vec2 cellUv = (cellId + 0.5) * cell / u_resolution;
  vec2 g = fract(px / cell);                  // 0..1 inside cell

  // sample cell average colour (small offset cross-tap = cheap blur)
  vec3 cc = texture(u_tex, cellUv).rgb;
  cc += texture(u_tex, cellUv + vec2( cell,0.0)/u_resolution).rgb;
  cc += texture(u_tex, cellUv + vec2(-cell,0.0)/u_resolution).rgb;
  cc += texture(u_tex, cellUv + vec2(0.0, cell)/u_resolution).rgb;
  cc *= 0.25;

  float L = luma(cc);
  L = clamp((L-0.5)*u_p1 + 0.5, 0.0, 1.0);    // contrast around mid
  // subtle scan flicker so glyphs shimmer alive
  L *= 0.9 + 0.1*sin(u_time*6.0 + cellId.x*1.7 + cellId.y*0.9);

  int idx = int(clamp(floor(L*7.0), 0.0, 6.0));
  float ink = glyph(idx, g);

  // terminal-phosphor tint: keep source hue but push toward green glow
  vec3 tint = cc * vec3(0.55,1.35,0.7);
  float bright = 0.6 + 0.4*L;
  vec3 col = tint * ink * bright;
  // faint phosphor bloom in empty cells so it reads as a CRT terminal
  col += vec3(0.02,0.06,0.03) * (1.0-ink) * L;
  // cell border darkening for that mosaic grid feel
  float border = smoothstep(0.0,0.06,g.x)*smoothstep(0.0,0.06,g.y)
               * smoothstep(0.0,0.06,1.0-g.x)*smoothstep(0.0,0.06,1.0-g.y);
  col *= 0.55 + 0.45*border;

  fragColor = vec4(mix(src, col, clamp(u_mix, 0.0, 1.0)), 1.0);
}
