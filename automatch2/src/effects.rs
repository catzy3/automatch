//! Effect graph — a linear chain of GLSL fragment-shader passes run over a
//! frame with ping-pong FBOs. Each effect declares the shared uniform contract
//! (see `shaders/common.glsl`) and mixes itself in by `u_mix`.
//!
//! Shaders are `include_str!`d from `automatch2/shaders/` so they live as real
//! files (portable GLSL, the v1 asset) yet get bundled into the binary.

#![allow(dead_code)]

use crate::gfx::{self, make_tex_rgba, PingPong, Program, Quad};
use eframe::glow::{self, HasContext};

/// One shader pass with its on/off state and dry/wet mix.
pub struct Effect {
    pub name: &'static str,
    pub enabled: bool,
    pub mix: f32,
    prog: Program,
}

impl Effect {
    unsafe fn new(gl: &glow::Context, name: &'static str, frag: &str) -> Result<Self, String> {
        Ok(Self {
            name,
            enabled: false,
            mix: 1.0,
            prog: Program::new(gl, gfx::VS_FULLSCREEN, frag)
                .map_err(|e| format!("{name}: {e}"))?,
        })
    }

    /// Featured effects are user-facing; names starting with '_' are internal
    /// (e.g. the selftest invert).
    pub fn is_featured(&self) -> bool {
        !self.name.starts_with('_')
    }
}

/// Built-in effects: (name, fragment source). Diverse on purpose — warp,
/// dispersion, CRT, channel split — not "squares/recolors".
fn builtin() -> Vec<(&'static str, &'static str)> {
    vec![
        ("rgb_split", include_str!("../shaders/rgb_split.frag")),
        ("wave_warp", include_str!("../shaders/wave_warp.frag")),
        ("chroma_aberration", include_str!("../shaders/chroma_aberration.frag")),
        ("crt_scanlines", include_str!("../shaders/crt_scanlines.frag")),
        ("vhs", include_str!("../shaders/vhs.frag")),
        ("shockwave", include_str!("../shaders/shockwave.frag")),
        ("pixel_stretch", include_str!("../shaders/pixel_stretch.frag")),
        ("_invert", include_str!("../shaders/_invert.frag")),
    ]
}

pub struct EffectChain {
    quad: Quad,
    pp: Option<PingPong>,
    input_tex: Option<glow::Texture>,
    size: (i32, i32),
    pub effects: Vec<Effect>,
}

impl EffectChain {
    pub unsafe fn new(gl: &glow::Context) -> Result<Self, String> {
        let quad = Quad::new(gl)?;
        let mut effects = Vec::new();
        for (name, src) in builtin() {
            effects.push(Effect::new(gl, name, src)?);
        }
        Ok(Self { quad, pp: None, input_tex: None, size: (0, 0), effects })
    }

    /// Enable an effect by name with a given mix (others untouched).
    pub fn set(&mut self, name: &str, enabled: bool, mix: f32) {
        if let Some(e) = self.effects.iter_mut().find(|e| e.name == name) {
            e.enabled = enabled;
            e.mix = mix;
        }
    }

    unsafe fn ensure(&mut self, gl: &glow::Context, w: i32, h: i32) -> Result<(), String> {
        if self.size == (w, h) && self.pp.is_some() {
            return Ok(());
        }
        if let Some(pp) = self.pp.take() {
            pp.destroy(gl);
        }
        if let Some(t) = self.input_tex.take() {
            gl.delete_texture(t);
        }
        self.input_tex = Some(make_tex_rgba(gl, w, h, None)?);
        self.pp = Some(PingPong::new(gl, w, h)?);
        self.size = (w, h);
        Ok(())
    }

    /// Run the enabled effects over `input` (tightly packed RGBA, w*h*4) and
    /// read the result back to CPU. If nothing is enabled, returns the input
    /// unchanged. (Readback is a Phase-3 convenience; Phase 6/8 will keep the
    /// result on the GPU and paint it straight into the viewport.)
    pub unsafe fn process(
        &mut self,
        gl: &glow::Context,
        input: &[u8],
        w: i32,
        h: i32,
        time: f32,
    ) -> Result<Vec<u8>, String> {
        self.ensure(gl, w, h)?;
        let itex = self.input_tex.unwrap();
        gl.bind_texture(glow::TEXTURE_2D, Some(itex));
        gl.tex_image_2d(
            glow::TEXTURE_2D,
            0,
            glow::RGBA8 as i32,
            w,
            h,
            0,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            Some(input),
        );
        gl.bind_texture(glow::TEXTURE_2D, None);

        let active: Vec<usize> = self
            .effects
            .iter()
            .enumerate()
            .filter(|(_, e)| e.enabled && e.mix > 0.001)
            .map(|(i, _)| i)
            .collect();
        if active.is_empty() {
            return Ok(input.to_vec());
        }

        gl.disable(glow::BLEND);
        let mut src_tex = itex;
        let mut last_fbo: Option<glow::Framebuffer> = None;
        for &i in &active {
            let (dfbo, dtex) = {
                let d = self.pp.as_ref().unwrap().dst();
                (d.fbo, d.tex)
            };
            gl.bind_framebuffer(glow::FRAMEBUFFER, Some(dfbo));
            gl.viewport(0, 0, w, h);
            {
                let eff = &self.effects[i];
                eff.prog.bind(gl);
                gl.active_texture(glow::TEXTURE0);
                gl.bind_texture(glow::TEXTURE_2D, Some(src_tex));
                eff.prog.set_i32(gl, "u_tex", 0);
                eff.prog.set_f32(gl, "u_mix", eff.mix);
                eff.prog.set_f32(gl, "u_time", time);
                eff.prog.set_vec2(gl, "u_resolution", w as f32, h as f32);
            }
            self.quad.draw(gl);
            src_tex = dtex;
            last_fbo = Some(dfbo);
            self.pp.as_mut().unwrap().swap();
        }

        let mut out = vec![0u8; (w * h * 4) as usize];
        gl.bind_framebuffer(glow::FRAMEBUFFER, last_fbo);
        gl.read_pixels(
            0,
            0,
            w,
            h,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            glow::PixelPackData::Slice(&mut out),
        );
        gl.bind_framebuffer(glow::FRAMEBUFFER, None);
        Ok(out)
    }

    pub unsafe fn destroy(self, gl: &glow::Context) {
        self.quad.destroy(gl);
        if let Some(pp) = self.pp {
            pp.destroy(gl);
        }
        if let Some(t) = self.input_tex {
            gl.delete_texture(t);
        }
        for e in self.effects {
            e.prog.destroy(gl);
        }
    }
}

/// Headless check of the chain machinery: enable the deterministic `_invert`
/// pass over a known 2×2 colour and verify the readback is inverted.
pub fn selftest(gl: &glow::Context) -> Result<String, String> {
    unsafe {
        let mut chain = EffectChain::new(gl)?;
        for e in chain.effects.iter_mut() {
            e.enabled = e.name == "_invert";
            e.mix = 1.0;
        }
        let color = [10u8, 150, 240, 255];
        let mut input = Vec::with_capacity(2 * 2 * 4);
        for _ in 0..4 {
            input.extend_from_slice(&color);
        }
        let out = chain.process(gl, &input, 2, 2, 0.0)?;
        let got = (out[0] as i32, out[1] as i32, out[2] as i32);
        let want = (245, 105, 15); // 255 - (10,150,240)
        let ok = (got.0 - want.0).abs() <= 2
            && (got.1 - want.1).abs() <= 2
            && (got.2 - want.2).abs() <= 2;
        chain.destroy(gl);
        if ok {
            Ok(format!("invert got=({},{},{}) want=({},{},{})", got.0, got.1, got.2, want.0, want.1, want.2))
        } else {
            Err(format!("invert got=({},{},{}) want=({},{},{})", got.0, got.1, got.2, want.0, want.1, want.2))
        }
    }
}
