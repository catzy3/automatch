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
        ("kaleidoscope", include_str!("../shaders/kaleidoscope.frag")),
        ("halftone", include_str!("../shaders/halftone.frag")),
        ("scan_glitch", include_str!("../shaders/scan_glitch.frag")),
        ("echo_trails", include_str!("../shaders/echo_trails.frag")),
        ("feedback_zoom", include_str!("../shaders/feedback_zoom.frag")),
        ("time_displacement", include_str!("../shaders/time_displacement.frag")),
        ("optic_flow_mosh", include_str!("../shaders/optic_flow_mosh.frag")),
        ("_invert", include_str!("../shaders/_invert.frag")),
    ]
}

pub struct EffectChain {
    quad: Quad,
    pp: Option<PingPong>,
    input_tex: Option<glow::Texture>,
    feedback_tex: Option<glow::Texture>, // previous chain output (u_prev)
    history_tex: Option<glow::Texture>,  // 2D-array ring of recent inputs (u_history)
    history_head: i32,                   // next write slot
    size: (i32, i32),
    pub effects: Vec<Effect>,
}

/// Frames kept in the time-displacement history ring (~0.8 s at 30 fps).
const HISTORY_N: i32 = 24;

impl EffectChain {
    pub unsafe fn new(gl: &glow::Context) -> Result<Self, String> {
        let quad = Quad::new(gl)?;
        let mut effects = Vec::new();
        for (name, src) in builtin() {
            effects.push(Effect::new(gl, name, src)?);
        }
        Ok(Self {
            quad,
            pp: None,
            input_tex: None,
            feedback_tex: None,
            history_tex: None,
            history_head: 0,
            size: (0, 0),
            effects,
        })
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
        if let Some(t) = self.feedback_tex.take() {
            gl.delete_texture(t);
        }
        if let Some(t) = self.history_tex.take() {
            gl.delete_texture(t);
        }
        self.input_tex = Some(make_tex_rgba(gl, w, h, None)?);
        // feedback starts black so the first frame has no ghost
        let zeros = vec![0u8; (w * h * 4) as usize];
        self.feedback_tex = Some(make_tex_rgba(gl, w, h, Some(&zeros))?);
        // history ring: a 2D-array texture with HISTORY_N layers (NEAREST so
        // each scanline pulls one exact past frame, no layer blending)
        let ht = gl.create_texture()?;
        gl.bind_texture(glow::TEXTURE_2D_ARRAY, Some(ht));
        gl.tex_image_3d(
            glow::TEXTURE_2D_ARRAY, 0, glow::RGBA8 as i32,
            w, h, HISTORY_N, 0, glow::RGBA, glow::UNSIGNED_BYTE, None,
        );
        gl.tex_parameter_i32(glow::TEXTURE_2D_ARRAY, glow::TEXTURE_MIN_FILTER, glow::NEAREST as i32);
        gl.tex_parameter_i32(glow::TEXTURE_2D_ARRAY, glow::TEXTURE_MAG_FILTER, glow::NEAREST as i32);
        gl.tex_parameter_i32(glow::TEXTURE_2D_ARRAY, glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE as i32);
        gl.tex_parameter_i32(glow::TEXTURE_2D_ARRAY, glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE as i32);
        gl.bind_texture(glow::TEXTURE_2D_ARRAY, None);
        self.history_tex = Some(ht);
        self.history_head = 0;
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

        // append the current input into the history ring (for time_displacement)
        if let Some(ht) = self.history_tex {
            gl.copy_image_sub_data(
                itex, glow::TEXTURE_2D, 0, 0, 0, 0,
                ht, glow::TEXTURE_2D_ARRAY, 0, 0, 0, self.history_head,
                w, h, 1,
            );
            self.history_head = (self.history_head + 1) % HISTORY_N;
        }

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
        // previous chain output → unit 1 (u_prev) for the whole pass
        gl.active_texture(glow::TEXTURE1);
        gl.bind_texture(glow::TEXTURE_2D, self.feedback_tex);
        // history ring → unit 2 (u_history)
        gl.active_texture(glow::TEXTURE2);
        gl.bind_texture(glow::TEXTURE_2D_ARRAY, self.history_tex);
        gl.active_texture(glow::TEXTURE0);
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
                eff.prog.set_i32(gl, "u_prev", 1); // feedback (skipped if undeclared)
                eff.prog.set_i32(gl, "u_history", 2);
                eff.prog.set_i32(gl, "u_history_n", HISTORY_N);
                eff.prog.set_i32(gl, "u_history_head", self.history_head);
                eff.prog.set_f32(gl, "u_mix", eff.mix);
                eff.prog.set_f32(gl, "u_time", time);
                eff.prog.set_vec2(gl, "u_resolution", w as f32, h as f32);
            }
            self.quad.draw(gl);
            src_tex = dtex;
            last_fbo = Some(dfbo);
            self.pp.as_mut().unwrap().swap();
        }

        gl.bind_framebuffer(glow::FRAMEBUFFER, last_fbo);
        // persist the final frame into feedback_tex for next call's u_prev
        if let Some(ft) = self.feedback_tex {
            gl.active_texture(glow::TEXTURE1);
            gl.bind_texture(glow::TEXTURE_2D, Some(ft));
            gl.copy_tex_sub_image_2d(glow::TEXTURE_2D, 0, 0, 0, 0, 0, w, h);
            gl.active_texture(glow::TEXTURE0);
        }
        let mut out = vec![0u8; (w * h * 4) as usize];
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
        if let Some(t) = self.feedback_tex {
            gl.delete_texture(t);
        }
        if let Some(t) = self.history_tex {
            gl.delete_texture(t);
        }
        for e in self.effects {
            e.prog.destroy(gl);
        }
    }
}

/// Headless check of the FEEDBACK path: with `echo_trails`, frame 1 of a green
/// input should pass through; after feeding a black frame, the trail (prev*0.92)
/// must persist — proving feedback_tex carries state across process() calls.
pub fn selftest_feedback(gl: &glow::Context) -> Result<String, String> {
    unsafe {
        let mut chain = EffectChain::new(gl)?;
        for e in chain.effects.iter_mut() {
            e.enabled = e.name == "echo_trails";
            e.mix = 1.0;
        }
        let green = [0u8, 200, 0, 255];
        let black = [0u8, 0, 0, 255];
        let mut in1 = Vec::new();
        let mut in0 = Vec::new();
        for _ in 0..4 {
            in1.extend_from_slice(&green);
            in0.extend_from_slice(&black);
        }
        let out1 = chain.process(gl, &in1, 2, 2, 0.0)?; // prev=black → ~green
        let out2 = chain.process(gl, &in0, 2, 2, 0.0)?; // prev=green → ~green*0.92
        let g1 = out1[1] as i32;
        let g2 = out2[1] as i32;
        chain.destroy(gl);
        // frame1 ~200; frame2 is the trail ~184 (must be a strong non-zero ghost)
        let ok = g1 >= 180 && g2 >= 150 && g2 <= 199;
        let msg = format!("frame1 G={g1}, trail(frame2 from black) G={g2}");
        if ok {
            Ok(msg)
        } else {
            Err(msg)
        }
    }
}

/// Headless check of the time-displacement history ring: feed frames of
/// increasing brightness, then confirm different scanlines return different
/// (older vs newer) frames — proving the per-row time offset works.
pub fn selftest_time(gl: &glow::Context) -> Result<String, String> {
    unsafe {
        let mut chain = EffectChain::new(gl)?;
        for e in chain.effects.iter_mut() {
            e.enabled = e.name == "time_displacement";
            e.mix = 1.0;
        }
        let (w, h) = (4, 8);
        let mut out = Vec::new();
        // feed 30 frames: solid green ramps up over time
        for k in 0..30 {
            let g = ((k * 9).min(255)) as u8;
            let mut frame = Vec::with_capacity((w * h * 4) as usize);
            for _ in 0..(w * h) {
                frame.extend_from_slice(&[0, g, 0, 255]);
            }
            out = chain.process(gl, &frame, w, h, 0.0)?;
        }
        // compare the two extreme scanlines (different delays → different past)
        let g_bottom = out[1] as i32; // row 0
        let g_top = out[((h - 1) * w * 4 + 1) as usize] as i32; // last row
        chain.destroy(gl);
        let ok = (g_bottom - g_top).abs() >= 40; // clear time spread across rows
        let msg = format!("row0 G={g_bottom}, row{} G={g_top} (Δ={})", h - 1, (g_bottom - g_top).abs());
        if ok {
            Ok(msg)
        } else {
            Err(msg)
        }
    }
}

/// Runtime smoke test of `optic_flow_mosh`: push a moving edge through several
/// frames and confirm the pass is stable (valid output) and actually alters the
/// frame (uses the feedback + history infra without producing garbage/NaN).
pub fn selftest_flow(gl: &glow::Context) -> Result<String, String> {
    unsafe {
        let mut chain = EffectChain::new(gl)?;
        for e in chain.effects.iter_mut() {
            e.enabled = e.name == "optic_flow_mosh";
            e.mix = 1.0;
        }
        let (w, h) = (8, 4);
        let frame = |split: i32| -> Vec<u8> {
            let mut f = Vec::with_capacity((w * h * 4) as usize);
            for _y in 0..h {
                for x in 0..w {
                    let v = if x < split { 0u8 } else { 255u8 };
                    f.extend_from_slice(&[v, v, v, 255]);
                }
            }
            f
        };
        let mut out = Vec::new();
        for k in 0..6 {
            out = chain.process(gl, &frame(2 + k % 5), w, h, 0.0)?; // edge slides
        }
        let last_in = frame(2 + 5 % 5);
        chain.destroy(gl);
        if out.len() != (w * h * 4) as usize {
            return Err("bad output size".into());
        }
        // not identical to the raw input → the mosh actually did something
        let changed = out != last_in;
        // sanity: a mix of values present (not collapsed to a single constant)
        let mn = *out.iter().min().unwrap();
        let mx = *out.iter().max().unwrap();
        if changed && mx > mn {
            Ok(format!("stable over 6 frames, range {mn}..{mx}, altered=yes"))
        } else {
            Err(format!("changed={changed}, range {mn}..{mx}"))
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
