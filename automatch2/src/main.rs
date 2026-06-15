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
mod montage;
mod timeline;

/// Minimal video player: decodes frames on the main thread, paced by wall clock,
/// loops at end of stream. Display + effects happen in the app `update`.
struct Player {
    ffmpeg: PathBuf,
    src: PathBuf,
    reader: Option<media::VideoReader>,
    montage: Option<montage::MontageFrameSource>, // Some → beat-sync montage mode
    duration: f32,                                // montage total length
    clock: Instant,                               // montage playback clock
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
            montage: None,
            duration: 0.0,
            clock: Instant::now(),
            buf: Vec::new(),
            size,
            last_advance: Instant::now(),
            fps,
            frame_idx: 0,
        })
    }

    /// Open in montage mode: a clip pool driven by the beat-sync timeline.
    fn open_montage(
        ffmpeg: PathBuf,
        mont: montage::MontageFrameSource,
        duration: f32,
        fps: f32,
    ) -> Self {
        Self {
            ffmpeg,
            src: PathBuf::from("<montage>"),
            reader: None,
            montage: Some(mont),
            duration,
            clock: Instant::now(),
            buf: Vec::new(),
            size: [0, 0],
            last_advance: Instant::now(),
            fps,
            frame_idx: 0,
        }
    }

    /// Decode the next frame if it's time. Returns true when `buf` holds a fresh,
    /// correctly-sized frame this tick.
    fn advance_decode(&mut self) -> bool {
        let dt = 1.0 / self.fps.max(1.0);
        if self.last_advance.elapsed().as_secs_f32() < dt {
            return false;
        }
        self.last_advance = Instant::now();

        // ── montage mode: pull from the timeline-driven clip source ──
        if self.montage.is_some() {
            let mut t = self.clock.elapsed().as_secs_f32();
            if self.duration > 0.0 {
                t %= self.duration;
            }
            let got = self.montage.as_mut().unwrap().frame_at(t, &mut self.buf);
            if got {
                if let Some((w, h)) = self.montage.as_ref().unwrap().dimensions() {
                    self.size = [w as usize, h as usize];
                }
                self.frame_idx += 1;
            }
            return got && self.buf.len() == self.size[0] * self.size[1] * 4;
        }

        // ── single-clip mode ──
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
    // bitstream datamosh (Avidemux-style) applied as an EXPORT post-pass
    datamosh_bs: datamosh::DatamoshOpts,
    datamosh_bs_on: bool,
    // (clip pool, cuts, duration, beats) — set when a montage folder is loaded,
    // so export can build a fresh frame source.
    montage_plan: Option<(montage::ClipPool, Vec<timeline::Cut>, f32, Vec<f32>)>,
    player: Option<Player>,
    chain: Option<effects::EffectChain>,
    blitter: Option<gfx::Blitter>,
    result_tex: Option<eframe::glow::Texture>, // GPU effect result (painted directly)
    result_size: [usize; 2],
    start: Instant,
    frames: u64,
}

impl AutomatchApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        apply_retro_theme(&cc.egui_ctx);
        // Cheap CPU/GL selftests always run; the ffmpeg-spawning ones
        // (media/audio-file/export/datamosh) only with AM2_SELFTEST set, so a
        // normal launch isn't delayed by ~4 ffmpeg passes.
        let run_st = std::env::var("AM2_SELFTEST").is_ok();
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

        let (media_selftest, media_ok) = if run_st {
            match media::selftest() {
                Ok(d) => (format!("PASS — {d}"), true),
                Err(e) => (format!("FAIL — {e}"), false),
            }
        } else {
            ("off (AM2_SELFTEST)".to_owned(), true)
        };
        if run_st {
            eprintln!("MEDIA SELFTEST: {media_selftest}");
        }

        let (chain, effects_selftest, effects_ok) = match cc.gl.as_ref() {
            Some(gl) => {
                let (msg, ok) = match effects::selftest(gl) {
                    Ok(d) => (format!("PASS — {d}"), true),
                    Err(e) => (format!("FAIL — {e}"), false),
                };
                eprintln!("EFFECTS SELFTEST: {msg}");
                match effects::selftest_feedback(gl) {
                    Ok(d) => eprintln!("FEEDBACK SELFTEST: PASS — {d}"),
                    Err(e) => eprintln!("FEEDBACK SELFTEST: FAIL — {e}"),
                }
                match effects::selftest_time(gl) {
                    Ok(d) => eprintln!("TIMEDISP SELFTEST: PASS — {d}"),
                    Err(e) => eprintln!("TIMEDISP SELFTEST: FAIL — {e}"),
                }
                match effects::selftest_flow(gl) {
                    Ok(d) => eprintln!("FLOWMOSH SELFTEST: PASS — {d}"),
                    Err(e) => eprintln!("FLOWMOSH SELFTEST: FAIL — {e}"),
                }
                match effects::selftest_beat(gl) {
                    Ok(d) => eprintln!("BEAT SELFTEST: PASS — {d}"),
                    Err(e) => eprintln!("BEAT SELFTEST: FAIL — {e}"),
                }
                match effects::selftest_params(gl) {
                    Ok(d) => eprintln!("PARAMS SELFTEST: PASS — {d}"),
                    Err(e) => eprintln!("PARAMS SELFTEST: FAIL — {e}"),
                }
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

        let (audio_file_selftest, audio_file_ok) = if run_st {
            match audio::selftest_file() {
                Ok(d) => (format!("PASS — {d}"), true),
                Err(e) => (format!("FAIL — {e}"), false),
            }
        } else {
            ("off (AM2_SELFTEST)".to_owned(), true)
        };
        if run_st {
            eprintln!("AUDIO-FILE SELFTEST: {audio_file_selftest}");
        }

        let (timeline_selftest, timeline_ok) = match timeline::selftest() {
            Ok(d) => (format!("PASS — {d}"), true),
            Err(e) => (format!("FAIL — {e}"), false),
        };
        eprintln!("TIMELINE SELFTEST: {timeline_selftest}");

        let (export_selftest, export_ok) = match (run_st, cc.gl.as_ref()) {
            (true, Some(gl)) => match export::selftest(gl) {
                Ok(d) => (format!("PASS — {d}"), true),
                Err(e) => (format!("FAIL — {e}"), false),
            },
            (true, None) => ("skipped (no gl)".to_owned(), false),
            (false, _) => ("off (AM2_SELFTEST)".to_owned(), true),
        };
        if run_st {
            eprintln!("EXPORT SELFTEST: {export_selftest}");
        }

        let (datamosh_selftest, datamosh_ok) = if run_st {
            match datamosh::selftest() {
                Ok(d) => (format!("PASS — {d}"), true),
                Err(e) => (format!("FAIL — {e}"), false),
            }
        } else {
            ("off (AM2_SELFTEST)".to_owned(), true)
        };
        if run_st {
            eprintln!("DATAMOSH SELFTEST: {datamosh_selftest}");
        }

        if run_st {
            match montage::selftest() {
                Ok(d) => eprintln!("MONTAGE SELFTEST: PASS — {d}"),
                Err(e) => eprintln!("MONTAGE SELFTEST: FAIL — {e}"),
            }
        }

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

        // AM2_MONTAGE_DIR=<folder> boots straight into montage mode (also the
        // headless check of the montage player + export path); else the demo clip.
        let (player, montage_plan) = if let Ok(dir) = std::env::var("AM2_MONTAGE_DIR") {
            let pool = montage::ClipPool::from_folder(&PathBuf::from(&dir));
            if pool.is_empty() {
                eprintln!("MONTAGE PLAYER: no clips in {dir}");
                (None, None)
            } else {
                let dur = 4.0f32;
                let beats: Vec<f32> = (0..8).map(|k| k as f32 * 0.5).collect();
                let cuts = timeline::build_timeline(&beats, dur, 1.0, pool.len(), 1);
                eprintln!("MONTAGE PLAYER: {} clips, {} cuts", pool.len(), cuts.len());
                let plan = (pool.clone(), cuts.clone(), dur, beats.clone());
                let mont = montage::MontageFrameSource::new(pool, cuts);
                (Some(Player::open_montage(media::ffmpeg_path(), mont, dur, 30.0)), Some(plan))
            }
        } else {
            let ffmpeg = media::ffmpeg_path();
            let demo = std::env::temp_dir().join("am2_demo.mp4");
            let p = match media::generate_test_clip(&ffmpeg, &demo, 640, 360, 6.0)
                .and_then(|_| Player::open(ffmpeg, demo, 30.0))
            {
                Ok(p) => Some(p),
                Err(e) => {
                    eprintln!("DEMO PLAYER: {e}");
                    None
                }
            };
            (p, None)
        };

        // blitter paints the GPU effect result straight into the egui viewport
        let blitter = cc
            .gl
            .as_ref()
            .and_then(|gl| unsafe { gfx::Blitter::new(gl) }.ok());

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
            datamosh_bs: datamosh::DatamoshOpts::default(),
            datamosh_bs_on: std::env::var("AM2_DATAMOSH").is_ok(), // headless verify hook

            montage_plan,
            player,
            chain,
            blitter,
            result_tex: None,
            result_size: [0, 0],
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
                // beat pulse from loaded audio analysis + playback time
                let beat = self.analysis.as_ref().map_or(0.0, |a| {
                    if a.duration <= 0.0 || a.beats.is_empty() {
                        return 0.0;
                    }
                    let pt = (p.frame_idx as f32 / p.fps) % a.duration;
                    let mut last = -1.0f32;
                    for &b in &a.beats {
                        if b <= pt {
                            last = b;
                        } else {
                            break;
                        }
                    }
                    if last < 0.0 { 0.0 } else { (-6.0 * (pt - last)).exp() }
                });
                // run the chain on the GPU and keep the result texture (no
                // CPU readback) — painted straight into the viewport below.
                if let (Some(gl), Some(chain)) = (frame.gl(), self.chain.as_mut()) {
                    chain.set_beat(beat);
                    if let Some(mx) = mod_chroma {
                        chain.set("chroma_aberration", true, mx);
                    }
                    if p.buf.len() == (w * h * 4) as usize {
                        self.result_tex =
                            unsafe { chain.process_to_texture(gl, &p.buf, w, h, time) }.ok();
                        self.result_size = [w as usize, h as usize];
                    }
                }
            }
        }

        egui::SidePanel::right("inspector").min_width(310.0).show(ctx, |ui| {
            ui.heading("automatch 2");
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
            if ui.button("📂 Загрузить видео…").clicked() {
                if let Some(path) = rfd::FileDialog::new()
                    .add_filter("video", &["mp4", "mov", "mkv", "webm", "avi", "m4v"])
                    .pick_file()
                {
                    match Player::open(media::ffmpeg_path(), path, 30.0) {
                        Ok(p) => {
                            self.player = Some(p);
                            self.result_tex = None;
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
            if ui.button("📁 Папка клипов → монтаж…").clicked() {
                if let Some(dir) = rfd::FileDialog::new().pick_folder() {
                    let pool = montage::ClipPool::from_folder(&dir);
                    if pool.is_empty() {
                        self.export_status = "в папке нет видео".to_owned();
                    } else {
                        // timeline from loaded audio beats, else uniform 120 BPM
                        let (beats, dur) = match self.analysis.as_ref() {
                            Some(a) => (a.beats.clone(), a.duration),
                            None => {
                                let dur = 12.0;
                                let beats: Vec<f32> =
                                    (0..(dur as i32 * 2)).map(|k| k as f32 * 0.5).collect();
                                (beats, dur)
                            }
                        };
                        let cuts = timeline::build_timeline(&beats, dur, 1.0, pool.len(), 1);
                        let n = pool.len();
                        self.montage_plan =
                            Some((pool.clone(), cuts.clone(), dur, beats.clone()));
                        let mont = montage::MontageFrameSource::new(pool, cuts);
                        self.player =
                            Some(Player::open_montage(media::ffmpeg_path(), mont, dur, 30.0));
                        self.result_tex = None;
                        self.export_status = format!("монтаж: {n} клипов, {dur:.0}s");
                    }
                }
            }
            ui.separator();
            let c = |ok: bool| {
                if ok {
                    egui::Color32::from_rgb(80, 220, 120)
                } else {
                    egui::Color32::from_rgb(230, 80, 80)
                }
            };
            // dev diagnostics tucked away — not in the user's face
            ui.collapsing("⚙ Диагностика", |ui| {
                ui.label(format!("OpenGL: {}", self.gl_version));
                let dt = ctx.input(|i| i.unstable_dt).max(1e-4);
                ui.label(format!("FPS: {:.0}   кадров: {}", 1.0 / dt, self.frames));
                ui.separator();
                ui.colored_label(c(self.gfx_ok), format!("GFX: {}", self.gfx_selftest));
                ui.colored_label(c(self.media_ok), format!("MEDIA: {}", self.media_selftest));
                ui.colored_label(c(self.effects_ok), format!("EFFECTS: {}", self.effects_selftest));
                ui.colored_label(c(self.audio_ok), format!("AUDIO: {}", self.audio_selftest));
                ui.colored_label(c(self.audio_file_ok), format!("AUDIO-FILE: {}", self.audio_file_selftest));
                ui.colored_label(c(self.timeline_ok), format!("TIMELINE: {}", self.timeline_selftest));
                ui.colored_label(c(self.export_ok), format!("EXPORT: {}", self.export_selftest));
                ui.colored_label(c(self.datamosh_ok), format!("DATAMOSH: {}", self.datamosh_selftest));
                ui.colored_label(c(self.modmatrix_ok), format!("MODMATRIX: {}", self.modmatrix_selftest));
            });
            ui.separator();
            ui.checkbox(&mut self.mod_demo, "ModMatrix демо: LFO-пульс chroma");
            ui.separator();

            ui.strong("Пресеты (1 клик)");
            if let Some(chain) = self.chain.as_mut() {
                ui.horizontal_wrapped(|ui| {
                    for (name, items) in presets() {
                        if ui.button(name).clicked() {
                            chain.disable_all();
                            for (eff, mix, params) in &items {
                                chain.set(eff, true, *mix);
                                for (idx, val) in params {
                                    chain.set_param(eff, *idx, *val);
                                }
                            }
                        }
                    }
                });
            }
            ui.separator();

            ui.strong("Цепочка эффектов");
            if let Some(chain) = self.chain.as_mut() {
                for e in chain.effects.iter_mut().filter(|e| e.is_featured()) {
                    ui.checkbox(&mut e.enabled, e.name);
                    if e.enabled {
                        ui.indent(e.name, |ui| {
                            ui.add(egui::Slider::new(&mut e.mix, 0.0..=1.0).text("mix"));
                            for prm in e.params.iter_mut() {
                                ui.add(
                                    egui::Slider::new(&mut prm.value, prm.min..=prm.max)
                                        .text(prm.label),
                                );
                            }
                        });
                    }
                }
            } else {
                ui.colored_label(c(false), "цепочка недоступна");
            }

            ui.separator();
            ui.collapsing("🩻 Датамош (Avidemux, на экспорте)", |ui| {
                ui.checkbox(&mut self.datamosh_bs_on, "Включить на экспорте");
                let on = self.datamosh_bs_on;
                let d = &mut self.datamosh_bs;
                ui.add_enabled_ui(on, |ui| {
                    ui.checkbox(&mut d.bloom, "Bloom — удалять I-кадры (расплыв)");
                    ui.checkbox(&mut d.smear, "Smear — дублить P-кадры (смаз)");
                    ui.add(egui::Slider::new(&mut d.keyframe_sec, 0.25..=8.0).text("Интервал I, сек"));
                    ui.add(egui::Slider::new(&mut d.bloom_chance, 0.0..=1.0).text("Доля удаляемых I"));
                    ui.add(egui::Slider::new(&mut d.smear_repeat, 1..=8).text("Повторов P"));
                    ui.add(egui::Slider::new(&mut d.smear_chance, 0.0..=1.0).text("Доля дублей P"));
                    ui.add(egui::Slider::new(&mut d.fine_scale, 1.0..=4.0).text("Детализация (мельче блоки)"));
                });
            });

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
            }); // ScrollArea
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
            ui.heading("Превью");
            match (self.result_tex, self.blitter) {
                (Some(tex), Some(blit)) if self.result_size[0] > 0 => {
                    // allocate a rect with the video's aspect, fit into the panel
                    let avail = ui.available_size();
                    let aspect = self.result_size[0] as f32 / self.result_size[1] as f32;
                    let mut w = avail.x;
                    let mut h = w / aspect;
                    if h > avail.y {
                        h = avail.y;
                        w = h * aspect;
                    }
                    let (rect, _) = ui.allocate_exact_size(egui::vec2(w, h), egui::Sense::hover());
                    let cb = eframe::egui_glow::CallbackFn::new(move |info, painter| {
                        let vp = info.viewport_in_pixels();
                        unsafe {
                            blit.draw(
                                painter.gl(),
                                tex,
                                (vp.left_px as i32, vp.from_bottom_px as i32,
                                 vp.width_px as i32, vp.height_px as i32),
                                true, // flip Y: GL FBO is bottom-up vs egui top-down
                            );
                        }
                    });
                    ui.painter().add(egui::PaintCallback {
                        rect,
                        callback: std::sync::Arc::new(cb),
                    });
                }
                _ => {
                    ui.label("декодирование…");
                }
            }
        });

        // ---- run a requested export (here frame.gl() is available) ----
        if let Some(out) = self.export_request.take() {
            let ff = media::ffmpeg_path();
            let mut status = "экспорт пропущен (нет GL/цепочки)".to_owned();
            let plan = self.montage_plan.clone();
            let src_fps = self.player.as_ref().map(|p| (p.src.clone(), p.fps));
            if let (Some(gl), Some(chain)) = (frame.gl(), self.chain.as_mut()) {
                status = if let Some((pool, cuts, dur, beats)) = plan {
                    // ── full montage export ──
                    let mut mont = montage::MontageFrameSource::new(pool, cuts);
                    match unsafe {
                        export::export_montage(gl, &ff, &mut mont, &out, chain, 30.0, dur, &beats)
                    } {
                        Ok(n) => format!("✓ монтаж {n} кадров → {}", out.display()),
                        Err(e) => format!("ошибка монтажа: {e}"),
                    }
                } else if let Some((src, fps)) = src_fps {
                    // ── single-clip export ──
                    match unsafe { export::export_clip(gl, &ff, &src, &out, chain, fps) } {
                        Ok(n) => format!("✓ {n} кадров → {}", out.display()),
                        Err(e) => format!("ошибка экспорта: {e}"),
                    }
                } else {
                    "нет источника".to_owned()
                };
            }
            // ── bitstream datamosh post-pass (Avidemux-style), like v1 ──
            if self.datamosh_bs_on && status.starts_with('✓') && out.exists() {
                let moshed = out.with_extension("mosh.mp4");
                let (w, h) = media::probe_dimensions(&ff, &out).unwrap_or((1280, 720));
                let dur = media::probe_duration(&ff, &out).unwrap_or(10.0);
                match datamosh::datamosh_video(&out, &moshed, &self.datamosh_bs, 30.0, dur, 7, w, h) {
                    Ok((nin, nout)) => {
                        if std::fs::rename(&moshed, &out).is_ok() {
                            status.push_str(&format!(" + датамош({nin}→{nout})"));
                        }
                    }
                    Err(e) => status.push_str(&format!(" (датамош пропущен: {e})")),
                }
            }
            self.export_status = status;
        }

        ctx.request_repaint();
    }
}

/// One-click looks: (название, [(эффект, mix, [(param_idx, value)])]).
type Preset = (&'static str, Vec<(&'static str, f32, Vec<(usize, f32)>)>);

fn presets() -> Vec<Preset> {
    vec![
        ("Чисто", vec![]),
        ("Датамош (v1)", vec![
            ("datamosh", 1.0, vec![(0, 0.7), (1, 0.7), (2, 2.5), (3, 0.08), (5, 0.6)]),
            ("chroma_aberration", 0.5, vec![(0, 1.4)]),
        ]),
        ("Флоу-мош", vec![
            ("optic_flow_mosh", 1.0, vec![(0, 2.0)]),
            ("scan_glitch", 0.5, vec![(0, 1.6)]),
        ]),
        ("Ретро VHS", vec![
            ("vhs", 0.9, vec![(0, 1.4)]),
            ("crt_scanlines", 0.6, vec![]),
            ("rgb_split", 0.5, vec![(0, 1.2)]),
        ]),
        ("Дрим", vec![
            ("feedback_zoom", 0.7, vec![(0, 1.6)]),
            ("light_leaks", 0.6, vec![(0, 1.3)]),
            ("chroma_aberration", 0.4, vec![]),
        ]),
        ("Калейдо", vec![
            ("kaleidoscope", 1.0, vec![(0, 8.0), (1, 1.5)]),
            ("chroma_aberration", 0.4, vec![]),
        ]),
        ("Время", vec![
            ("time_displacement", 0.8, vec![]),
            ("echo_trails", 0.5, vec![(0, 0.9)]),
        ]),
        ("Неон", vec![
            ("neon_edge", 0.9, vec![(0, 1.4)]),
            ("feedback_zoom", 0.4, vec![(0, 1.0)]),
        ]),
        ("Бит-удар", vec![
            ("beat_punch", 1.0, vec![(0, 1.5)]),
            ("rgb_split", 0.4, vec![]),
        ]),
    ]
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
