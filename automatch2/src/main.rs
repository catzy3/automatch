//! AutoMatch 2 — Phases 0–2 scaffold.
//!
//!   * window (winit, via eframe)
//!   * egui immediate-mode UI
//!   * glow (OpenGL) context + GFX core (see `gfx`)
//!   * media decode → on-screen video player (see `media`)
//!
//! Self-tests for the GFX pipeline and media decode run at startup and report
//! PASS/FAIL in the panel and on stderr, so each layer is proven, not assumed.

use eframe::egui;
use std::path::PathBuf;
use std::time::Instant;

mod gfx;
mod media;

/// Minimal video player: decodes frames on the main thread, paced by wall clock,
/// uploads each to an egui texture, loops at end of stream.
struct Player {
    ffmpeg: PathBuf,
    src: PathBuf,
    reader: Option<media::VideoReader>,
    tex: Option<egui::TextureHandle>,
    buf: Vec<u8>,
    size: [usize; 2],
    last_advance: Instant,
    fps: f32,
    frame_idx: u64,
}

impl Player {
    fn open(ffmpeg: PathBuf, src: PathBuf, fps: f32) -> Result<Self, String> {
        let reader = media::VideoReader::open(&ffmpeg, &src)?;
        let size = [reader.w as usize, reader.h as usize];
        Ok(Self {
            ffmpeg,
            src,
            reader: Some(reader),
            tex: None,
            buf: Vec::new(),
            size,
            last_advance: Instant::now(),
            fps,
            frame_idx: 0,
        })
    }

    fn advance(&mut self, ctx: &egui::Context) {
        let dt = 1.0 / self.fps.max(1.0);
        if self.last_advance.elapsed().as_secs_f32() < dt {
            return;
        }
        self.last_advance = Instant::now();

        let got = match self.reader.as_mut() {
            Some(r) => r.next_frame(&mut self.buf).unwrap_or(false),
            None => false,
        };
        if !got {
            // end of stream → reopen (loop)
            self.reader = media::VideoReader::open(&self.ffmpeg, &self.src).ok();
            if let Some(r) = self.reader.as_mut() {
                let _ = r.next_frame(&mut self.buf);
            }
            self.frame_idx = 0;
        } else {
            self.frame_idx += 1;
        }

        if self.buf.len() == self.size[0] * self.size[1] * 4 {
            let img = egui::ColorImage::from_rgba_unmultiplied(self.size, &self.buf);
            match &mut self.tex {
                Some(t) => t.set(img, egui::TextureOptions::LINEAR),
                None => self.tex = Some(ctx.load_texture("video", img, egui::TextureOptions::LINEAR)),
            }
        }
    }
}

struct AutomatchApp {
    gl_version: String,
    gfx_selftest: String,
    gfx_ok: bool,
    media_selftest: String,
    media_ok: bool,
    player: Option<Player>,
    frames: u64,
}

impl AutomatchApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let (gl_version, gfx_selftest, gfx_ok) = match cc.gl.as_ref() {
            Some(gl) => {
                use eframe::glow::HasContext as _;
                let ver = unsafe { gl.get_parameter_string(eframe::glow::VERSION) };
                let (msg, ok) = match gfx::selftest(gl) {
                    Ok(d) => (format!("PASS — {d}"), true),
                    Err(e) => (format!("FAIL — {e}"), false),
                };
                eprintln!("GFX SELFTEST: {msg}");
                (ver, msg, ok)
            }
            None => ("<no GL context>".to_owned(), "skipped (no gl)".to_owned(), false),
        };

        let (media_selftest, media_ok) = match media::selftest() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("MEDIA SELFTEST: {media_selftest}");

        // Spin up a demo player on a freshly generated clip (until the file
        // picker lands in the UI phase).
        let player = {
            let ffmpeg = media::ffmpeg_path();
            let demo = std::env::temp_dir().join("am2_demo.mp4");
            match media::generate_test_clip(&ffmpeg, &demo, 640, 360, 6.0)
                .and_then(|_| Player::open(ffmpeg, demo, 30.0))
            {
                Ok(p) => Some(p),
                Err(e) => {
                    eprintln!("DEMO PLAYER: {e}");
                    None
                }
            }
        };

        Self {
            gl_version,
            gfx_selftest,
            gfx_ok,
            media_selftest,
            media_ok,
            player,
            frames: 0,
        }
    }
}

impl eframe::App for AutomatchApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.frames += 1;
        if let Some(p) = self.player.as_mut() {
            p.advance(ctx);
        }

        egui::SidePanel::left("status").min_width(360.0).show(ctx, |ui| {
            ui.heading("automatch 2");
            ui.label("Phases 0–2 — окно + GFX + медиа-декод");
            ui.separator();
            ui.label(format!("OpenGL: {}", self.gl_version));
            let dt = ctx.input(|i| i.unstable_dt).max(1e-4);
            ui.label(format!("FPS: {:.0}   frames: {}", 1.0 / dt, self.frames));
            ui.separator();

            let c = |ok: bool| {
                if ok {
                    egui::Color32::from_rgb(80, 220, 120)
                } else {
                    egui::Color32::from_rgb(230, 80, 80)
                }
            };
            ui.colored_label(c(self.gfx_ok), format!("GFX selftest: {}", self.gfx_selftest));
            ui.add_space(4.0);
            ui.colored_label(c(self.media_ok), format!("MEDIA selftest: {}", self.media_selftest));
            ui.add_space(8.0);
            match self.player.as_ref() {
                Some(p) => ui.label(format!("плеер: {}x{} кадр #{}", p.size[0], p.size[1], p.frame_idx)),
                None => ui.colored_label(c(false), "плеер: нет (ffmpeg?)"),
            };
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("Превью (демо-клип)");
            if let Some(p) = self.player.as_ref() {
                if let Some(tex) = p.tex.as_ref() {
                    let avail = ui.available_size();
                    let aspect = p.size[0] as f32 / p.size[1] as f32;
                    let mut w = avail.x.min(p.size[0] as f32 * 2.0);
                    let mut h = w / aspect;
                    if h > avail.y {
                        h = avail.y;
                        w = h * aspect;
                    }
                    ui.image(egui::load::SizedTexture::new(tex.id(), egui::vec2(w, h)));
                } else {
                    ui.label("декодирование первого кадра…");
                }
            } else {
                ui.label("плеер недоступен");
            }
        });

        ctx.request_repaint(); // keep the player animating
    }
}

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1100.0, 640.0])
            .with_title("automatch 2 — C2 media"),
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };
    eframe::run_native(
        "automatch 2 — C2 media",
        native_options,
        Box::new(|cc| Ok(Box::new(AutomatchApp::new(cc)))),
    )
}
