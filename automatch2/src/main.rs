//! AutoMatch 2 — Phase 0 scaffold.
//!
//! Proves the foundation of the C++→Rust port works end to end:
//!   * a window (winit, via eframe),
//!   * an egui immediate-mode UI panel,
//!   * a live OpenGL context (glow) we can later render effects into.
//!
//! Next phases build the GFX engine (ping-pong FBO, GLSL passes), media I/O,
//! the effect graph, audio/timeline and the full UI on top of this.

use eframe::egui;

struct AutomatchApp {
    /// OpenGL version string read once at startup — proof the glow context is live.
    gl_version: String,
    frames: u64,
}

impl AutomatchApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let gl_version = match cc.gl.as_ref() {
            Some(gl) => {
                use eframe::glow::HasContext as _;
                unsafe { gl.get_parameter_string(eframe::glow::VERSION) }
            }
            None => "<no GL context>".to_owned(),
        };
        Self { gl_version, frames: 0 }
    }
}

impl eframe::App for AutomatchApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.frames += 1;
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("automatch 2");
            ui.label("Phase 0 — scaffold (winit + glow + egui via eframe)");
            ui.separator();
            ui.label(format!("OpenGL: {}", self.gl_version));
            let dt = ctx.input(|i| i.unstable_dt).max(1e-4);
            ui.label(format!("FPS: {:.0}", 1.0 / dt));
            ui.label(format!("frames: {}", self.frames));
            ui.add_space(8.0);
            ui.label("✓ окно, egui-панель и GL-контекст работают");
        });
        // animate so the FPS/frame counters update continuously
        ctx.request_repaint();
    }
}

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([900.0, 560.0])
            .with_title("automatch 2 — C0 scaffold"),
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };
    eframe::run_native(
        "automatch 2 — C0 scaffold",
        native_options,
        Box::new(|cc| Ok(Box::new(AutomatchApp::new(cc)))),
    )
}
