//! Core GFX primitives over `glow` (OpenGL): shader programs with a uniform
//! location cache, a fullscreen quad, and offscreen framebuffers. This is the
//! foundation every effect pass is built on.
//!
//! Everything here is `unsafe` because the GL API is — callers must hold a
//! current context (eframe gives us one). A built-in `selftest` exercises the
//! whole compile → upload → draw → readback path so we can prove the pipeline
//! works headlessly (no eyeballing required).

// Several setters/helpers are part of the public GFX surface used by later
// phases (effect passes); allow them to sit unused for now.
#![allow(dead_code)]

use eframe::glow::{self, HasContext};
use std::cell::RefCell;
use std::collections::HashMap;

/// A linked shader program with a lazy uniform-location cache.
pub struct Program {
    pub raw: glow::Program,
    cache: RefCell<HashMap<String, Option<glow::UniformLocation>>>,
}

impl Program {
    /// Compile + link a vertex/fragment program. Returns the GL info log on error.
    pub unsafe fn new(gl: &glow::Context, vs: &str, fs: &str) -> Result<Self, String> {
        let raw = gl.create_program()?;
        let v = compile(gl, glow::VERTEX_SHADER, vs)?;
        let f = compile(gl, glow::FRAGMENT_SHADER, fs)?;
        gl.attach_shader(raw, v);
        gl.attach_shader(raw, f);
        gl.link_program(raw);
        let ok = gl.get_program_link_status(raw);
        gl.detach_shader(raw, v);
        gl.detach_shader(raw, f);
        gl.delete_shader(v);
        gl.delete_shader(f);
        if !ok {
            let log = gl.get_program_info_log(raw);
            gl.delete_program(raw);
            return Err(format!("link failed: {log}"));
        }
        Ok(Self { raw, cache: RefCell::new(HashMap::new()) })
    }

    /// Cached uniform location lookup.
    pub unsafe fn loc(&self, gl: &glow::Context, name: &str) -> Option<glow::UniformLocation> {
        if let Some(v) = self.cache.borrow().get(name) {
            return v.clone();
        }
        let v = gl.get_uniform_location(self.raw, name);
        self.cache.borrow_mut().insert(name.to_owned(), v.clone());
        v
    }

    pub unsafe fn bind(&self, gl: &glow::Context) {
        gl.use_program(Some(self.raw));
    }

    pub unsafe fn set_i32(&self, gl: &glow::Context, name: &str, v: i32) {
        if let Some(l) = self.loc(gl, name) {
            gl.uniform_1_i32(Some(&l), v);
        }
    }

    pub unsafe fn set_f32(&self, gl: &glow::Context, name: &str, v: f32) {
        if let Some(l) = self.loc(gl, name) {
            gl.uniform_1_f32(Some(&l), v);
        }
    }

    pub unsafe fn set_vec2(&self, gl: &glow::Context, name: &str, x: f32, y: f32) {
        if let Some(l) = self.loc(gl, name) {
            gl.uniform_2_f32(Some(&l), x, y);
        }
    }

    pub unsafe fn destroy(self, gl: &glow::Context) {
        gl.delete_program(self.raw);
    }
}

unsafe fn compile(gl: &glow::Context, kind: u32, src: &str) -> Result<glow::Shader, String> {
    let s = gl.create_shader(kind)?;
    gl.shader_source(s, src);
    gl.compile_shader(s);
    if !gl.get_shader_compile_status(s) {
        let log = gl.get_shader_info_log(s);
        gl.delete_shader(s);
        let what = if kind == glow::VERTEX_SHADER { "vertex" } else { "fragment" };
        return Err(format!("{what} shader compile failed: {log}"));
    }
    Ok(s)
}

/// Fullscreen quad (triangle strip) with position + UV attributes.
pub struct Quad {
    vao: glow::VertexArray,
    vbo: glow::Buffer,
}

impl Quad {
    pub unsafe fn new(gl: &glow::Context) -> Result<Self, String> {
        // x, y, u, v  (NDC + texcoords), as a triangle strip
        let verts: [f32; 16] = [
            -1.0, -1.0, 0.0, 0.0,
             1.0, -1.0, 1.0, 0.0,
            -1.0,  1.0, 0.0, 1.0,
             1.0,  1.0, 1.0, 1.0,
        ];
        let vao = gl.create_vertex_array()?;
        let vbo = gl.create_buffer()?;
        gl.bind_vertex_array(Some(vao));
        gl.bind_buffer(glow::ARRAY_BUFFER, Some(vbo));
        let bytes = core::slice::from_raw_parts(
            verts.as_ptr() as *const u8,
            verts.len() * core::mem::size_of::<f32>(),
        );
        gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes, glow::STATIC_DRAW);
        let stride = 4 * core::mem::size_of::<f32>() as i32;
        gl.enable_vertex_attrib_array(0);
        gl.vertex_attrib_pointer_f32(0, 2, glow::FLOAT, false, stride, 0);
        gl.enable_vertex_attrib_array(1);
        gl.vertex_attrib_pointer_f32(1, 2, glow::FLOAT, false, stride, 2 * core::mem::size_of::<f32>() as i32);
        gl.bind_vertex_array(None);
        Ok(Self { vao, vbo })
    }

    pub unsafe fn draw(&self, gl: &glow::Context) {
        gl.bind_vertex_array(Some(self.vao));
        gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
        gl.bind_vertex_array(None);
    }

    pub unsafe fn destroy(self, gl: &glow::Context) {
        gl.delete_vertex_array(self.vao);
        gl.delete_buffer(self.vbo);
    }
}

/// Create an RGBA8 texture, optionally seeded with pixel data (tightly packed).
pub unsafe fn make_tex_rgba(
    gl: &glow::Context,
    w: i32,
    h: i32,
    data: Option<&[u8]>,
) -> Result<glow::Texture, String> {
    let t = gl.create_texture()?;
    gl.bind_texture(glow::TEXTURE_2D, Some(t));
    gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MIN_FILTER, glow::LINEAR as i32);
    gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MAG_FILTER, glow::LINEAR as i32);
    gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE as i32);
    gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE as i32);
    gl.tex_image_2d(
        glow::TEXTURE_2D,
        0,
        glow::RGBA8 as i32,
        w,
        h,
        0,
        glow::RGBA,
        glow::UNSIGNED_BYTE,
        data,
    );
    gl.bind_texture(glow::TEXTURE_2D, None);
    Ok(t)
}

/// An offscreen framebuffer backed by one RGBA8 color texture.
pub struct Fbo {
    pub fbo: glow::Framebuffer,
    pub tex: glow::Texture,
    pub w: i32,
    pub h: i32,
}

impl Fbo {
    pub unsafe fn new(gl: &glow::Context, w: i32, h: i32) -> Result<Self, String> {
        let tex = make_tex_rgba(gl, w, h, None)?;
        let fbo = gl.create_framebuffer()?;
        gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fbo));
        gl.framebuffer_texture_2d(
            glow::FRAMEBUFFER,
            glow::COLOR_ATTACHMENT0,
            glow::TEXTURE_2D,
            Some(tex),
            0,
        );
        let status = gl.check_framebuffer_status(glow::FRAMEBUFFER);
        gl.bind_framebuffer(glow::FRAMEBUFFER, None);
        if status != glow::FRAMEBUFFER_COMPLETE {
            return Err(format!("framebuffer incomplete: {status:#x}"));
        }
        Ok(Self { fbo, tex, w, h })
    }

    pub unsafe fn bind(&self, gl: &glow::Context) {
        gl.bind_framebuffer(glow::FRAMEBUFFER, Some(self.fbo));
        gl.viewport(0, 0, self.w, self.h);
    }

    pub unsafe fn destroy(self, gl: &glow::Context) {
        gl.delete_framebuffer(self.fbo);
        gl.delete_texture(self.tex);
    }
}

/// Two FBOs that alternate as source/destination for an effect chain.
/// Each pass reads `src()` and draws into `dst()`, then you `swap()`.
pub struct PingPong {
    a: Fbo,
    b: Fbo,
    flip: bool,
}

impl PingPong {
    pub unsafe fn new(gl: &glow::Context, w: i32, h: i32) -> Result<Self, String> {
        Ok(Self { a: Fbo::new(gl, w, h)?, b: Fbo::new(gl, w, h)?, flip: false })
    }

    pub fn src(&self) -> &Fbo {
        if self.flip { &self.b } else { &self.a }
    }

    pub fn dst(&self) -> &Fbo {
        if self.flip { &self.a } else { &self.b }
    }

    pub fn swap(&mut self) {
        self.flip = !self.flip;
    }

    pub unsafe fn destroy(self, gl: &glow::Context) {
        self.a.destroy(gl);
        self.b.destroy(gl);
    }
}

/// Blits a texture into the currently-bound framebuffer at egui's viewport —
/// used to paint the GPU effect result straight into the egui panel (no CPU
/// readback). Holds only raw GL handles so it is Copy+Send+Sync and can be
/// captured by an egui paint callback.
#[derive(Clone, Copy)]
pub struct Blitter {
    program: glow::Program,
    vao: glow::VertexArray,
    vbo: glow::Buffer,
    loc_tex: Option<glow::UniformLocation>,
    loc_flip: Option<glow::UniformLocation>,
}

const FS_BLIT: &str = r#"#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform int u_flip;
void main() {
    vec2 uv = (u_flip == 1) ? vec2(v_uv.x, 1.0 - v_uv.y) : v_uv;
    fragColor = vec4(texture(u_tex, uv).rgb, 1.0);
}
"#;

impl Blitter {
    pub unsafe fn new(gl: &glow::Context) -> Result<Self, String> {
        // compile a tiny passthrough program directly (no RefCell Program — must
        // stay Copy for the paint-callback closure)
        let prog = Program::new(gl, VS_FULLSCREEN, FS_BLIT)?;
        let program = prog.raw;
        let loc_tex = gl.get_uniform_location(program, "u_tex");
        let loc_flip = gl.get_uniform_location(program, "u_flip");
        let verts: [f32; 16] = [
            -1.0, -1.0, 0.0, 0.0,  1.0, -1.0, 1.0, 0.0,
            -1.0,  1.0, 0.0, 1.0,  1.0,  1.0, 1.0, 1.0,
        ];
        let vao = gl.create_vertex_array()?;
        let vbo = gl.create_buffer()?;
        gl.bind_vertex_array(Some(vao));
        gl.bind_buffer(glow::ARRAY_BUFFER, Some(vbo));
        let bytes = core::slice::from_raw_parts(
            verts.as_ptr() as *const u8, verts.len() * 4);
        gl.buffer_data_u8_slice(glow::ARRAY_BUFFER, bytes, glow::STATIC_DRAW);
        let stride = 4 * 4;
        gl.enable_vertex_attrib_array(0);
        gl.vertex_attrib_pointer_f32(0, 2, glow::FLOAT, false, stride, 0);
        gl.enable_vertex_attrib_array(1);
        gl.vertex_attrib_pointer_f32(1, 2, glow::FLOAT, false, stride, 8);
        gl.bind_vertex_array(None);
        Ok(Self { program, vao, vbo, loc_tex, loc_flip })
    }

    /// Draw `tex` into egui's current viewport rect.
    pub unsafe fn draw(&self, gl: &glow::Context, tex: glow::Texture, vp: (i32, i32, i32, i32), flip: bool) {
        let (x, y, w, h) = vp;
        gl.viewport(x, y, w, h);
        gl.disable(glow::BLEND);
        gl.use_program(Some(self.program));
        gl.active_texture(glow::TEXTURE0);
        gl.bind_texture(glow::TEXTURE_2D, Some(tex));
        if let Some(l) = self.loc_tex.as_ref() {
            gl.uniform_1_i32(Some(l), 0);
        }
        if let Some(l) = self.loc_flip.as_ref() {
            gl.uniform_1_i32(Some(l), if flip { 1 } else { 0 });
        }
        gl.bind_vertex_array(Some(self.vao));
        gl.draw_arrays(glow::TRIANGLE_STRIP, 0, 4);
        gl.bind_vertex_array(None);
    }
}

pub const VS_FULLSCREEN: &str = r#"#version 330 core
layout(location=0) in vec2 in_pos;
layout(location=1) in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"#;

// Passthrough that swizzles RGB→BGR, so the readback proves the *fragment
// shader actually ran* (not just a raw blit).
const FS_SWIZZLE: &str = r#"#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
void main() {
    fragColor = vec4(texture(u_tex, v_uv).bgr, 1.0);
}
"#;

/// Headless end-to-end check of the GL pipeline: upload a known colour,
/// run it through one GLSL pass into an FBO, read it back, verify the swizzle.
/// Returns a human-readable detail string on success.
pub fn selftest(gl: &glow::Context) -> Result<String, String> {
    unsafe {
        let prog = Program::new(gl, VS_FULLSCREEN, FS_SWIZZLE)?;
        let quad = Quad::new(gl)?;

        // 4×4 texture filled with a known colour
        let color = [10u8, 150, 240, 255];
        let mut input = Vec::with_capacity(4 * 4 * 4);
        for _ in 0..(4 * 4) {
            input.extend_from_slice(&color);
        }
        let intex = make_tex_rgba(gl, 4, 4, Some(&input))?;
        let fbo = Fbo::new(gl, 4, 4)?;

        fbo.bind(gl);
        gl.clear_color(0.0, 0.0, 0.0, 1.0);
        gl.clear(glow::COLOR_BUFFER_BIT);
        prog.bind(gl);
        gl.active_texture(glow::TEXTURE0);
        gl.bind_texture(glow::TEXTURE_2D, Some(intex));
        prog.set_i32(gl, "u_tex", 0);
        quad.draw(gl);

        let mut buf = [0u8; 4 * 4 * 4];
        gl.read_pixels(
            0,
            0,
            4,
            4,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            glow::PixelPackData::Slice(&mut buf),
        );
        gl.bind_framebuffer(glow::FRAMEBUFFER, None);

        let got = (buf[0] as i32, buf[1] as i32, buf[2] as i32);
        let want = (240, 150, 10); // bgr of (10,150,240)
        let ok = (got.0 - want.0).abs() <= 2
            && (got.1 - want.1).abs() <= 2
            && (got.2 - want.2).abs() <= 2;

        // cleanup
        gl.delete_texture(intex);
        fbo.destroy(gl);
        quad.destroy(gl);
        prog.destroy(gl);

        if ok {
            Ok(format!("got=({},{},{}) want=({},{},{})", got.0, got.1, got.2, want.0, want.1, want.2))
        } else {
            Err(format!("got=({},{},{}) want=({},{},{})", got.0, got.1, got.2, want.0, want.1, want.2))
        }
    }
}
