// Shared uniform contract for every automatch2 effect fragment shader.
// (Documentation only — shaders inline these declarations so each file stays a
//  complete, standalone GLSL program.)
//
//   in  vec2 v_uv;                 // 0..1 texcoords from the fullscreen quad
//   out vec4 fragColor;
//   uniform sampler2D u_tex;       // current source frame
//   uniform float     u_mix;       // 0..1 dry/wet — each effect mixes itself in
//   uniform float     u_time;      // seconds since start (animation)
//   uniform vec2      u_resolution;// frame size in pixels
