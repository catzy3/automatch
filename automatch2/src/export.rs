//! Export — encode processed frames to an mp4 via an ffmpeg child process.
//!
//! Frames are produced on the GPU (effect chain) and piped as raw RGBA to
//! ffmpeg's stdin. The native bitstream-datamosh post-pass (port of v1's
//! datamosh_bitstream.py) plugs in here in a later sub-checkpoint.

#![allow(dead_code)]

use crate::effects::EffectChain;
use crate::media::{self, VideoReader};
use eframe::glow;
use std::io::Write;
use std::path::Path;
use std::process::{Child, Command, Stdio};

/// Pipes raw RGBA frames into ffmpeg and muxes an H.264 mp4.
pub struct Encoder {
    child: Child,
    w: i32,
    h: i32,
}

impl Encoder {
    pub fn new(ffmpeg: &Path, out: &Path, w: i32, h: i32, fps: f32) -> Result<Self, String> {
        let child = Command::new(ffmpeg)
            .args(["-y", "-hide_banner", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgba",
                   "-s", &format!("{w}x{h}"),
                   "-r", &format!("{fps}"),
                   "-i", "pipe:0",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-preset", "veryfast"])
            .arg(out)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("spawn ffmpeg encoder failed: {e}"))?;
        Ok(Self { child, w, h })
    }

    pub fn write_frame(&mut self, rgba: &[u8]) -> Result<(), String> {
        if rgba.len() != (self.w * self.h * 4) as usize {
            return Err(format!("frame size mismatch: {} != {}", rgba.len(), self.w * self.h * 4));
        }
        self.child
            .stdin
            .as_mut()
            .ok_or("encoder stdin closed")?
            .write_all(rgba)
            .map_err(|e| format!("write frame failed: {e}"))
    }

    pub fn finish(mut self) -> Result<(), String> {
        // closing stdin signals EOF to ffmpeg
        drop(self.child.stdin.take());
        let status = self.child.wait().map_err(|e| format!("encoder wait failed: {e}"))?;
        if status.success() {
            Ok(())
        } else {
            Err(format!("encoder exited with {status}"))
        }
    }
}

/// Render every frame of `src` through `chain` and encode to `out`.
/// Returns the number of frames written.
pub unsafe fn export_clip(
    gl: &glow::Context,
    ffmpeg: &Path,
    src: &Path,
    out: &Path,
    chain: &mut EffectChain,
    fps: f32,
) -> Result<u64, String> {
    let mut reader = VideoReader::open(ffmpeg, src)?;
    let (w, h) = (reader.w, reader.h);
    let mut enc = Encoder::new(ffmpeg, out, w, h, fps)?;
    let mut buf = Vec::new();
    let mut n = 0u64;
    loop {
        match reader.next_frame(&mut buf).map_err(|e| format!("decode: {e}"))? {
            true => {
                let t = n as f32 / fps.max(1.0);
                let processed = chain.process(gl, &buf, w, h, t)?;
                enc.write_frame(&processed)?;
                n += 1;
            }
            false => break,
        }
    }
    drop(reader);
    enc.finish()?;
    Ok(n)
}

/// Beat pulse (0..1) at time `t` from a beat grid — matches the live preview.
fn beat_at(beats: &[f32], dur: f32, t: f32) -> f32 {
    if beats.is_empty() || dur <= 0.0 {
        return 0.0;
    }
    let pt = t % dur;
    let mut last = -1.0f32;
    for &b in beats {
        if b <= pt {
            last = b;
        } else {
            break;
        }
    }
    if last < 0.0 {
        0.0
    } else {
        (-6.0 * (pt - last)).exp()
    }
}

/// Render a whole montage (clip pool + timeline) through the effect chain and
/// encode it — the full auto-editor output. `beats` drives beat-reactive
/// effects so the file matches the preview. Returns frames written.
pub unsafe fn export_montage(
    gl: &glow::Context,
    ffmpeg: &Path,
    mont: &mut crate::montage::MontageFrameSource,
    out: &Path,
    chain: &mut EffectChain,
    fps: f32,
    duration: f32,
    beats: &[f32],
) -> Result<u64, String> {
    let (w, h) = mont.canvas();
    let mut enc = Encoder::new(ffmpeg, out, w, h, fps)?;
    let mut buf = Vec::new();
    let total = (duration * fps.max(1.0)) as u64;
    let mut n = 0u64;
    for f in 0..total {
        let t = f as f32 / fps.max(1.0);
        if mont.frame_at(t, &mut buf) {
            chain.set_beat(beat_at(beats, duration, t));
            let processed = chain.process(gl, &buf, w, h, t)?;
            enc.write_frame(&processed)?;
            n += 1;
        }
    }
    enc.finish()?;
    Ok(n)
}

/// Headless end-to-end export check: generate a clip → decode → effect chain →
/// encode mp4 → re-decode the result and confirm it's a valid video.
pub fn selftest(gl: &glow::Context) -> Result<String, String> {
    let ffmpeg = media::ffmpeg_path();
    let tmp_in = std::env::temp_dir().join("am2_export_in.mp4");
    let tmp_out = std::env::temp_dir().join("am2_export_out.mp4");
    media::generate_test_clip(&ffmpeg, &tmp_in, 160, 90, 1.0)?;

    let written = unsafe {
        let mut chain = EffectChain::new(gl)?;
        chain.set("chroma_aberration", true, 0.7);
        let n = export_clip(gl, &ffmpeg, &tmp_in, &tmp_out, &mut chain, 30.0);
        chain.destroy(gl);
        n
    }?;

    // re-decode the exported file to prove it's a real, playable video
    let mut reader = VideoReader::open(&ffmpeg, &tmp_out)?;
    let (w, h) = (reader.w, reader.h);
    let mut buf = Vec::new();
    let mut read_back = 0u64;
    while reader.next_frame(&mut buf).unwrap_or(false) {
        read_back += 1;
        if read_back >= 10 {
            break;
        }
    }
    drop(reader);
    let _ = std::fs::remove_file(&tmp_in);
    let _ = std::fs::remove_file(&tmp_out);

    if w == 160 && h == 90 && written >= 20 && read_back >= 10 {
        Ok(format!("encoded {written} frames, re-decoded {w}x{h} ok"))
    } else {
        Err(format!("written={written}, re-decoded={read_back} {w}x{h}"))
    }
}
