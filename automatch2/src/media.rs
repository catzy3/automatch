//! Media I/O — decode video frames to raw RGBA via an ffmpeg child process.
//!
//! Phase 2 uses the pragmatic CLI-pipe path (same approach v1 used): spawn
//! ffmpeg, ask it for `rawvideo`/`rgba` on stdout, read frame-by-frame. A
//! later phase can swap in native libav (`ffmpeg-next`) behind the same API.

#![allow(dead_code)]

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

/// Locate an ffmpeg executable: env override → PATH → the imageio-ffmpeg
/// binary bundled with the v1 Python env → bare "ffmpeg" as a last resort.
pub fn ffmpeg_path() -> PathBuf {
    if let Ok(p) = std::env::var("AUTOMATCH_FFMPEG") {
        if Path::new(&p).exists() {
            return PathBuf::from(p);
        }
    }
    // imageio-ffmpeg bundles a versioned exe; glob its binaries dir.
    let imageio_dir = Path::new(r"C:\Python312\Lib\site-packages\imageio_ffmpeg\binaries");
    if imageio_dir.is_dir() {
        if let Ok(rd) = std::fs::read_dir(imageio_dir) {
            for e in rd.flatten() {
                let name = e.file_name().to_string_lossy().to_lowercase();
                if name.starts_with("ffmpeg") && name.ends_with(".exe") {
                    return e.path();
                }
            }
        }
    }
    PathBuf::from("ffmpeg")
}

/// Probe a video's pixel dimensions by parsing ffmpeg's stderr banner.
pub fn probe_dimensions(ffmpeg: &Path, file: &Path) -> Result<(i32, i32), String> {
    let out = Command::new(ffmpeg)
        .args(["-hide_banner", "-i"])
        .arg(file)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
    // ffmpeg exits non-zero here (no output file) — that's expected; parse stderr.
    let text = String::from_utf8_lossy(&out.stderr);
    parse_dimensions(&text).ok_or_else(|| "could not find WxH in ffmpeg output".to_owned())
}

/// Find the first `<num>x<num>` token (>= 2 each) — the video resolution.
fn parse_dimensions(s: &str) -> Option<(i32, i32)> {
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_digit() {
            let start = i;
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            if i < bytes.len() && bytes[i] == b'x' {
                let xpos = i;
                i += 1;
                let dstart = i;
                while i < bytes.len() && bytes[i].is_ascii_digit() {
                    i += 1;
                }
                if i > dstart {
                    let w: i32 = s[start..xpos].parse().ok()?;
                    let h: i32 = s[dstart..i].parse().ok()?;
                    if w >= 2 && h >= 2 && w <= 16384 && h <= 16384 {
                        return Some((w, h));
                    }
                }
            }
        } else {
            i += 1;
        }
    }
    None
}

/// Probe a clip's duration (seconds) by parsing ffmpeg's "Duration:" banner.
pub fn probe_duration(ffmpeg: &Path, file: &Path) -> Option<f32> {
    let out = Command::new(ffmpeg)
        .args(["-hide_banner", "-i"])
        .arg(file)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stderr);
    let idx = text.find("Duration:")?;
    let after = text[idx + "Duration:".len()..].trim_start();
    let hms = after.split(',').next()?.trim(); // HH:MM:SS.ss
    let mut parts = hms.split(':');
    let h: f32 = parts.next()?.parse().ok()?;
    let m: f32 = parts.next()?.parse().ok()?;
    let s: f32 = parts.next()?.parse().ok()?;
    Some(h * 3600.0 + m * 60.0 + s)
}

/// Streams a video as raw RGBA frames from an ffmpeg child process.
pub struct VideoReader {
    child: Child,
    stdout: std::io::BufReader<std::process::ChildStdout>,
    pub w: i32,
    pub h: i32,
    frame_bytes: usize,
}

impl VideoReader {
    pub fn open(ffmpeg: &Path, file: &Path) -> Result<Self, String> {
        let (w, h) = probe_dimensions(ffmpeg, file)?;
        let mut child = Command::new(ffmpeg)
            .args(["-hide_banner", "-loglevel", "error", "-i"])
            .arg(file)
            .args(["-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "no ffmpeg stdout".to_owned())?;
        Ok(Self {
            child,
            stdout: std::io::BufReader::new(stdout),
            w,
            h,
            frame_bytes: (w as usize) * (h as usize) * 4,
        })
    }

    /// Open a clip but scale every frame to a fixed `w`×`h` canvas (so a montage
    /// of differently-sized clips yields uniform frames). No probe needed.
    pub fn open_scaled(ffmpeg: &Path, file: &Path, w: i32, h: i32) -> Result<Self, String> {
        let mut child = Command::new(ffmpeg)
            .args(["-hide_banner", "-loglevel", "error", "-i"])
            .arg(file)
            .args(["-vf", &format!("scale={w}:{h}"),
                   "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
        let stdout = child.stdout.take().ok_or_else(|| "no ffmpeg stdout".to_owned())?;
        Ok(Self {
            child,
            stdout: std::io::BufReader::new(stdout),
            w,
            h,
            frame_bytes: (w as usize) * (h as usize) * 4,
        })
    }

    /// Like `open_scaled` but starts decoding `start_sec` into the clip
    /// (fast input seek via `-ss` before `-i`). Used for random montage
    /// in-points so a reused clip shows different footage each cut.
    pub fn open_scaled_at(
        ffmpeg: &Path,
        file: &Path,
        w: i32,
        h: i32,
        start_sec: f32,
    ) -> Result<Self, String> {
        if start_sec <= 1e-3 {
            return Self::open_scaled(ffmpeg, file, w, h);
        }
        let mut child = Command::new(ffmpeg)
            .args(["-hide_banner", "-loglevel", "error", "-ss", &format!("{start_sec:.3}"), "-i"])
            .arg(file)
            .args(["-vf", &format!("scale={w}:{h}"),
                   "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
        let stdout = child.stdout.take().ok_or_else(|| "no ffmpeg stdout".to_owned())?;
        Ok(Self {
            child,
            stdout: std::io::BufReader::new(stdout),
            w,
            h,
            frame_bytes: (w as usize) * (h as usize) * 4,
        })
    }

    pub fn frame_bytes(&self) -> usize {
        self.frame_bytes
    }

    /// Read the next frame into `buf`. Returns Ok(true) on a full frame,
    /// Ok(false) at end of stream.
    pub fn next_frame(&mut self, buf: &mut Vec<u8>) -> std::io::Result<bool> {
        buf.resize(self.frame_bytes, 0);
        match self.stdout.read_exact(buf) {
            Ok(()) => Ok(true),
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(false),
            Err(e) => Err(e),
        }
    }
}

impl Drop for VideoReader {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Generate a short synthetic test clip via ffmpeg (used by the selftest and as
/// a stand-in demo source until the file picker exists).
pub fn generate_test_clip(
    ffmpeg: &Path,
    out: &Path,
    w: i32,
    h: i32,
    seconds: f32,
) -> Result<(), String> {
    let size = format!("size={w}x{h}");
    let src = format!("testsrc2={size}:rate=30:duration={seconds}");
    let st = Command::new(ffmpeg)
        .args(["-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i"])
        .arg(&src)
        .args(["-pix_fmt", "yuv420p"])
        .arg(out)
        .status()
        .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
    if st.success() && out.exists() {
        Ok(())
    } else {
        Err("test clip generation failed".to_owned())
    }
}

/// Generate a solid-colour test clip (for montage tests / placeholders).
pub fn generate_color_clip(
    ffmpeg: &Path,
    out: &Path,
    color: &str,
    w: i32,
    h: i32,
    seconds: f32,
) -> Result<(), String> {
    let src = format!("color=c={color}:s={w}x{h}:r=30:d={seconds}");
    let st = Command::new(ffmpeg)
        .args(["-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i"])
        .arg(&src)
        .args(["-pix_fmt", "yuv420p"])
        .arg(out)
        .status()
        .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
    if st.success() && out.exists() {
        Ok(())
    } else {
        Err("color clip generation failed".to_owned())
    }
}

/// Headless check: generate a clip, decode several frames, verify dimensions
/// and that frames carry real content.
pub fn selftest() -> Result<String, String> {
    let ffmpeg = ffmpeg_path();
    let tmp = std::env::temp_dir().join("am2_media_selftest.mp4");
    generate_test_clip(&ffmpeg, &tmp, 160, 90, 1.0)?;

    let mut reader = VideoReader::open(&ffmpeg, &tmp)?;
    let mut buf = Vec::new();
    let mut n = 0;
    let mut nonzero = false;
    loop {
        match reader.next_frame(&mut buf) {
            Ok(true) => {
                n += 1;
                if !nonzero && buf.iter().any(|&b| b != 0) {
                    nonzero = true;
                }
                if n >= 5 {
                    break;
                }
            }
            Ok(false) => break,
            Err(e) => return Err(format!("read error: {e}")),
        }
    }
    let (w, h) = (reader.w, reader.h);
    drop(reader);
    let _ = std::fs::remove_file(&tmp);

    if w == 160 && h == 90 && n >= 5 && nonzero {
        Ok(format!("{w}x{h}, frames_read={n}, content=ok (ffmpeg: {})",
            ffmpeg.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()))
    } else {
        Err(format!("{w}x{h}, frames_read={n}, nonzero={nonzero}"))
    }
}
