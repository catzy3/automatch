//! AutoMatch 2 — Phases 0–3 scaffold.
//!
//!   * window (winit, via eframe) + egui UI + glow (OpenGL) context
//!   * GFX core (`gfx`): programs, quad, FBO, ping-pong
//!   * media decode → player (`media`)
//!   * effect graph (`effects`): GLSL passes over each frame, toggled live
//!
//! Each layer self-tests at startup (PASS/FAIL in the panel and on stderr).

use eframe::egui;
use std::path::PathBuf;
use std::time::Instant;

mod audio;
mod datamosh;
mod effects;
mod export;
mod gfx;
mod media;
mod modmatrix;
mod timeline;

/// Minimal video player: decodes frames on the main thread, paced by wall clock,
/// loops at end of stream. Display + effects happen in the app `update`.
struct Player {
    ffmpeg: PathBuf,
    src: PathBuf,
    reader: Option<media::VideoReader>,
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
            buf: Vec::new(),
            size,
            last_advance: Instant::now(),
            fps,
            frame_idx: 0,
        })
    }

    /// Decode the next frame if it's time. Returns true when `buf` holds a fresh,
    /// correctly-sized frame this tick.
    fn advance_decode(&mut self) -> bool {
        let dt = 1.0 / self.fps.max(1.0);
        if self.last_advance.elapsed().as_secs_f32() < dt {
            return false;
        }
        self.last_advance = Instant::now();

        let got = match self.reader.as_mut() {
            Some(r) => r.next_frame(&mut self.buf).unwrap_or(false),
            None => false,
        };
        if !got {
            self.reader = media::VideoReader::open(&self.ffmpeg, &self.src).ok();
            if let Some(r) = self.reader.as_mut() {
                let _ = r.next_frame(&mut self.buf);
            }
            self.frame_idx = 0;
        } else {
            self.frame_idx += 1;
        }
        self.buf.len() == self.size[0] * self.size[1] * 4
    }
}

struct AutomatchApp {
    gl_version: String,
    gfx_selftest: String,
    gfx_ok: bool,
    media_selftest: String,
    media_ok: bool,
    effects_selftest: String,
    effects_ok: bool,
    audio_selftest: String,
    audio_ok: bool,
    audio_file_selftest: String,
    audio_file_ok: bool,
    timeline_selftest: String,
    timeline_ok: bool,
    analysis: Option<audio::AudioAnalysis>,
    timeline_cuts: usize,
    export_selftest: String,
    export_ok: bool,
    datamosh_selftest: String,
    datamosh_ok: bool,
    modmatrix_selftest: String,
    modmatrix_ok: bool,
    modmatrix: modmatrix::ModMatrix,
    mod_demo: bool,
    export_request: Option<PathBuf>,
    export_status: String,
    player: Option<Player>,
    chain: Option<effects::EffectChain>,
    video_tex: Option<egui::TextureHandle>,
    start: Instant,
    frames: u64,
}

impl AutomatchApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        apply_retro_theme(&cc.egui_ctx);
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

        let (chain, effects_selftest, effects_ok) = match cc.gl.as_ref() {
            Some(gl) => {
                let (msg, ok) = match effects::selftest(gl) {
                    Ok(d) => (format!("PASS — {d}"), true),
                    Err(e) => (format!("FAIL — {e}"), false),
                };
                eprintln!("EFFECTS SELFTEST: {msg}");
                let chain = unsafe { effects::EffectChain::new(gl) }
                    .map(|mut c| {
                        // a tasteful default look on the demo clip
                        c.set("chroma_aberration", true, 0.6);
                        c.set("crt_scanlines", true, 0.45);
                        c
                    })
                    .ok();
                (chain, msg, ok)
            }
            None => (None, "skipped (no gl)".to_owned(), false),
        };

        let (audio_selftest, audio_ok) = match audio::selftest() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("AUDIO SELFTEST: {audio_selftest}");

        let (audio_file_selftest, audio_file_ok) = match audio::selftest_file() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("AUDIO-FILE SELFTEST: {audio_file_selftest}");

        let (timeline_selftest, timeline_ok) = match timeline::selftest() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("TIMELINE SELFTEST: {timeline_selftest}");

        let (export_selftest, export_ok) = match cc.gl.as_ref() {
            Some(gl) => match export::selftest(gl) {
                Ok(d) => (format!("PASS — {d}"), true),
                Err(e) => (format!("FAIL — {e}"), false),
            },
            None => ("skipped (no gl)".to_owned(), false),
        };
        eprintln!("EXPORT SELFTEST: {export_selftest}");

        let (datamosh_selftest, datamosh_ok) = match datamosh::selftest() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("DATAMOSH SELFTEST: {datamosh_selftest}");

        let (modmatrix_selftest, modmatrix_ok) = match modmatrix::selftest() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("MODMATRIX SELFTEST: {modmatrix_selftest}");

        // demo matrix: a slow sine pulsing the chroma aberration mix
        let modmatrix = modmatrix::ModMatrix {
            lfos: vec![modmatrix::Lfo::new(modmatrix::Wave::Sine, 0.4)],
            routes: vec![modmatrix::Route {
                lfo: 0,
                target: "chroma_aberration".into(),
                amount: 0.4,
            }],
        };

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
            effects_selftest,
            effects_ok,
            audio_selftest,
            audio_ok,
            audio_file_selftest,
            audio_file_ok,
            timeline_selftest,
            timeline_ok,
            analysis: None,
            timeline_cuts: 0,
            export_selftest,
            export_ok,
            datamosh_selftest,
            datamosh_ok,
            modmatrix_selftest,
            modmatrix_ok,
            modmatrix,
            mod_demo: false,
            // AM2_AUTOEXPORT=<path> triggers a one-shot export on first frame
            // (headless verification of the update-loop export path).
            export_request: std::env::var("AM2_AUTOEXPORT").ok().map(PathBuf::from),
            export_status: String::new(),
            player,
            chain,
            video_tex: None,
            start: Instant::now(),
            frames: 0,
        }
    }
}

impl eframe::App for AutomatchApp {
    fn update(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        self.frames += 1;
        let time = self.start.elapsed().as_secs_f32();

        // decode → effect chain → texture
        let new = self.player.as_mut().map(|p| p.advance_decode()).unwrap_or(false);
        if new {
            if let Some(p) = self.player.as_ref() {
                let w = p.size[0] as i32;
                let h = p.size[1] as i32;
                // ModMatrix demo: drive chroma mix with the LFO this frame
                let mod_chroma = if self.mod_demo {
                    self.modmatrix
                        .value_for("chroma_aberration", time)
                        .map(|m| (0.6 + m).clamp(0.0, 1.0))
                } else {
                    None
                };
                let processed: Option<Vec<u8>> = match (frame.gl(), self.chain.as_mut()) {
                    (Some(gl), Some(chain)) => {
                        if let Some(mx) = mod_chroma {
                            chain.set("chroma_aberration", true, mx);
                        }
                        unsafe { chain.process(gl, &p.buf, w, h, time).ok() }
                    }
                    _ => None,
                };
                let data = processed.unwrap_or_else(|| p.buf.clone());
                if data.len() == (w * h * 4) as usize {
                    let img = egui::ColorImage::from_rgba_unmultiplied([w as usize, h as usize], &data);
                    match &mut self.video_tex {
                        Some(t) => t.set(img, egui::TextureOptions::LINEAR),
                        None => self.video_tex = Some(ctx.load_texture("video", img, egui::TextureOptions::LINEAR)),
                    }
                }
            }
        }

        egui::SidePanel::left("status").min_width(330.0).show(ctx, |ui| {
            ui.heading("automatch 2");
            ui.label("C0–C5 + ModMatrix · окно/GFX/медиа/эффекты/аудио/экспорт");
            ui.separator();
            if ui.button("📂 Загрузить видео…").clicked() {
                if let Some(path) = rfd::FileDialog::new()
                    .add_filter("video", &["mp4", "mov", "mkv", "webm", "avi", "m4v"])
                    .pick_file()
                {
                    match Player::open(media::ffmpeg_path(), path, 30.0) {
                        Ok(p) => {
                            self.player = Some(p);
                            self.video_tex = None;
                        }
                        Err(e) => eprintln!("open video: {e}"),
                    }
                }
            }
            if let Some(p) = self.player.as_ref() {
                let name = p
                    .src
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
                ui.label(format!("видео: {name}"));
            }
            if ui.button("🎵 Загрузить аудио (анализ битов)…").clicked() {
                if let Some(path) = rfd::FileDialog::new()
                    .add_filter("audio", &["mp3", "wav", "flac", "m4a", "ogg", "aac", "opus"])
                    .pick_file()
                {
                    match audio::analyze(&media::ffmpeg_path(), &path) {
                        Ok(a) => {
                            let cuts = timeline::build_timeline(&a.beats, a.duration, 1.0, 8, 1);
                            self.timeline_cuts = cuts.len();
                            self.analysis = Some(a);
                        }
                        Err(e) => eprintln!("analyze audio: {e}"),
                    }
                }
            }
            if let Some(a) = self.analysis.as_ref() {
                ui.colored_label(
                    egui::Color32::from_rgb(120, 200, 255),
                    format!("BPM {:.1} · {} битов · {} cuts · {:.1}s",
                        a.bpm, a.beats.len(), self.timeline_cuts, a.duration),
                );
            }
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
            ui.colored_label(c(self.gfx_ok), format!("GFX: {}", self.gfx_selftest));
            ui.colored_label(c(self.media_ok), format!("MEDIA: {}", self.media_selftest));
            ui.colored_label(c(self.effects_ok), format!("EFFECTS: {}", self.effects_selftest));
            ui.colored_label(c(self.audio_ok), format!("AUDIO: {}", self.audio_selftest));
            ui.colored_label(c(self.audio_file_ok), format!("AUDIO-FILE: {}", self.audio_file_selftest));
            ui.colored_label(c(self.timeline_ok), format!("TIMELINE: {}", self.timeline_selftest));
            ui.colored_label(c(self.export_ok), format!("EXPORT: {}", self.export_selftest));
            ui.colored_label(c(self.datamosh_ok), format!("DATAMOSH: {}", self.datamosh_selftest));
            ui.colored_label(c(self.modmatrix_ok), format!("MODMATRIX: {}", self.modmatrix_selftest));
            ui.separator();
            ui.checkbox(&mut self.mod_demo, "ModMatrix демо: LFO-пульс chroma");
            ui.separator();

            ui.strong("Цепочка эффектов");
            if let Some(chain) = self.chain.as_mut() {
                for e in chain.effects.iter_mut().filter(|e| e.is_featured()) {
                    ui.horizontal(|ui| {
                        ui.checkbox(&mut e.enabled, e.name);
                    });
                    ui.add_enabled(
                        e.enabled,
                        egui::Slider::new(&mut e.mix, 0.0..=1.0).text("mix"),
                    );
                }
            } else {
                ui.colored_label(c(false), "цепочка недоступна");
            }

            ui.separator();
            let can_export = self.player.is_some() && self.chain.is_some();
            if ui
                .add_enabled(can_export, egui::Button::new("⬇ Экспорт MP4…"))
                .clicked()
            {
                let default_name = "automatch2_out.mp4";
                if let Some(path) = rfd::FileDialog::new()
                    .set_file_name(default_name)
                    .add_filter("mp4", &["mp4"])
                    .save_file()
                {
                    self.export_request = Some(path);
                    self.export_status = "экспортирую…".to_owned();
                }
            }
            if !self.export_status.is_empty() {
                ui.label(&self.export_status);
            }
        });

        egui::TopBottomPanel::bottom("timeline").min_height(86.0).show(ctx, |ui| {
            ui.label("Таймлайн (онсеты + биты)");
            let (resp, painter) = ui.allocate_painter(
                egui::vec2(ui.available_width(), 60.0),
                egui::Sense::hover(),
            );
            let rect = resp.rect;
            painter.rect_filled(rect, 0.0, egui::Color32::from_rgb(6, 6, 10));
            let cyan = egui::Color32::from_rgb(60, 220, 255);
            let magenta = egui::Color32::from_rgb(255, 46, 136);
            match self.analysis.as_ref() {
                Some(a) if !a.env.is_empty() && a.duration > 0.0 => {
                    let w = rect.width().max(1.0) as usize;
                    let n = a.env.len();
                    for x in 0..w {
                        let i = (x * n / w).min(n - 1);
                        let v = a.env[i].clamp(0.0, 1.0);
                        let px = rect.left() + x as f32;
                        painter.line_segment(
                            [egui::pos2(px, rect.bottom()), egui::pos2(px, rect.bottom() - v * rect.height())],
                            egui::Stroke::new(1.0, cyan.gamma_multiply(0.55)),
                        );
                    }
                    for &b in &a.beats {
                        let fx = (b / a.duration).clamp(0.0, 1.0);
                        let px = rect.left() + fx * rect.width();
                        painter.line_segment(
                            [egui::pos2(px, rect.top()), egui::pos2(px, rect.bottom())],
                            egui::Stroke::new(1.0, magenta),
                        );
                    }
                }
                _ => {
                    painter.text(
                        rect.center(),
                        egui::Align2::CENTER_CENTER,
                        "загрузите аудио для анализа битов",
                        egui::FontId::monospace(12.0),
                        egui::Color32::from_rgb(120, 120, 140),
                    );
                }
            }
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("Превью (демо-клип через цепочку)");
            match (self.player.as_ref(), self.video_tex.as_ref()) {
                (Some(p), Some(tex)) => {
                    let avail = ui.available_size();
                    let aspect = p.size[0] as f32 / p.size[1] as f32;
                    let mut w = avail.x.min(p.size[0] as f32 * 2.0);
                    let mut h = w / aspect;
                    if h > avail.y {
                        h = avail.y;
                        w = h * aspect;
                    }
                    ui.image(egui::load::SizedTexture::new(tex.id(), egui::vec2(w, h)));
                }
                _ => {
                    ui.label("декодирование…");
                }
            }
        });

        // ---- run a requested export (here frame.gl() is available) ----
        if let Some(out) = self.export_request.take() {
            let src_fps = self.player.as_ref().map(|p| (p.src.clone(), p.fps));
            let mut status = "экспорт пропущен (нет GL/видео/цепочки)".to_owned();
            if let (Some((src, fps)), Some(gl), Some(chain)) =
                (src_fps, frame.gl(), self.chain.as_mut())
            {
                let ff = media::ffmpeg_path();
                status = match unsafe { export::export_clip(gl, &ff, &src, &out, chain, fps) } {
                    Ok(n) => format!("✓ {n} кадров → {}", out.display()),
                    Err(e) => format!("ошибка экспорта: {e}"),
                };
            }
            self.export_status = status;
        }

        ctx.request_repaint();
    }
}

/// Retro-glitch egui theme: near-black panels, magenta/cyan accents, sharp
/// corners, monospace everywhere — the "shi o kiku" aesthetic.
fn apply_retro_theme(ctx: &egui::Context) {
    use egui::{Color32, Rounding, Stroke};
    let magenta = Color32::from_rgb(255, 46, 136);
    let cyan = Color32::from_rgb(60, 220, 255);
    let bg = Color32::from_rgb(10, 10, 16);
    let panel = Color32::from_rgb(16, 16, 24);
    let text = Color32::from_rgb(222, 226, 232);

    let mut style = (*ctx.style()).clone();
    let mut v = egui::Visuals::dark();
    v.panel_fill = panel;
    v.window_fill = bg;
    v.extreme_bg_color = Color32::from_rgb(6, 6, 10);
    v.faint_bg_color = Color32::from_rgb(22, 22, 32);
    v.override_text_color = Some(text);
    v.hyperlink_color = cyan;
    v.selection.bg_fill = magenta.gamma_multiply(0.45);
    v.selection.stroke = Stroke::new(1.0, magenta);
    v.window_rounding = Rounding::ZERO;
    v.menu_rounding = Rounding::ZERO;
    for w in [
        &mut v.widgets.noninteractive,
        &mut v.widgets.inactive,
        &mut v.widgets.hovered,
        &mut v.widgets.active,
        &mut v.widgets.open,
    ] {
        w.rounding = Rounding::ZERO;
    }
    v.widgets.hovered.bg_stroke = Stroke::new(1.0, cyan);
    v.widgets.active.bg_stroke = Stroke::new(1.0, magenta);
    style.visuals = v;
    style.override_text_style = Some(egui::TextStyle::Monospace);
    ctx.set_style(style);
}

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1180.0, 680.0])
            .with_title("automatch 2 — C3 effects"),
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };
    eframe::run_native(
        "automatch 2 — C3 effects",
        native_options,
        Box::new(|cc| Ok(Box::new(AutomatchApp::new(cc)))),
    )
}
