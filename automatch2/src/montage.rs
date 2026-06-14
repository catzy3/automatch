//! Montage engine — the auto-editor core: a pool of clips driven by the
//! beat-sync timeline, switching clips at each cut. This is what turns the
//! tool from an effects playground into an actual auto-editor.
//!
//! Phase: a frame source that, for a given playback time, decodes from the
//! clip the timeline assigned to the active cut, switching readers on cut
//! boundaries (clips loop if shorter than their cut). Random in-points,
//! transitions and prefetch are later refinements.

#![allow(dead_code)]

use crate::media::{ffmpeg_path, VideoReader};
use crate::timeline::Cut;
use std::path::{Path, PathBuf};

const VIDEO_EXTS: &[&str] = &["mp4", "mov", "mkv", "webm", "avi", "m4v"];

/// A set of source clips (e.g. a folder the user picked).
#[derive(Clone, Default)]
pub struct ClipPool {
    pub paths: Vec<PathBuf>,
}

impl ClipPool {
    pub fn from_paths(paths: Vec<PathBuf>) -> Self {
        Self { paths }
    }

    pub fn from_folder(dir: &Path) -> Self {
        let mut paths = Vec::new();
        if let Ok(rd) = std::fs::read_dir(dir) {
            for e in rd.flatten() {
                let p = e.path();
                let ext_ok = p
                    .extension()
                    .and_then(|s| s.to_str())
                    .map(|s| VIDEO_EXTS.contains(&s.to_lowercase().as_str()))
                    .unwrap_or(false);
                if ext_ok {
                    paths.push(p);
                }
            }
        }
        paths.sort();
        Self { paths }
    }

    pub fn len(&self) -> usize {
        self.paths.len()
    }

    pub fn is_empty(&self) -> bool {
        self.paths.is_empty()
    }
}

/// Decodes the montage: maps playback time → active cut → that cut's clip.
pub struct MontageFrameSource {
    ffmpeg: PathBuf,
    pool: ClipPool,
    cuts: Vec<Cut>,
    cur_cut: usize,
    reader: Option<VideoReader>,
}

impl MontageFrameSource {
    pub fn new(pool: ClipPool, cuts: Vec<Cut>) -> Self {
        Self {
            ffmpeg: ffmpeg_path(),
            pool,
            cuts,
            cur_cut: usize::MAX,
            reader: None,
        }
    }

    pub fn dimensions(&self) -> Option<(i32, i32)> {
        self.reader.as_ref().map(|r| (r.w, r.h))
    }

    fn cut_index_for(&self, t: f32) -> usize {
        for (i, c) in self.cuts.iter().enumerate() {
            if t >= c.t_start && t < c.t_end {
                return i;
            }
        }
        self.cuts.len().saturating_sub(1)
    }

    fn open_cut(&mut self, ci: usize) {
        self.cur_cut = ci;
        self.reader = None;
        if self.pool.is_empty() || ci >= self.cuts.len() {
            return;
        }
        let clip_idx = self.cuts[ci].clip_idx % self.pool.len();
        self.reader = VideoReader::open(&self.ffmpeg, &self.pool.paths[clip_idx]).ok();
    }

    /// Decode the next frame for playback time `t` into `buf`. Switches clips on
    /// cut boundaries; loops a clip that is shorter than its cut. Returns false
    /// if no frame could be produced.
    pub fn frame_at(&mut self, t: f32, buf: &mut Vec<u8>) -> bool {
        if self.cuts.is_empty() {
            return false;
        }
        let ci = self.cut_index_for(t);
        if ci != self.cur_cut || self.reader.is_none() {
            self.open_cut(ci);
        }
        match self.reader.as_mut() {
            Some(r) => match r.next_frame(buf) {
                Ok(true) => true,
                _ => {
                    // clip ended inside the cut → loop it
                    self.open_cut(ci);
                    matches!(self.reader.as_mut().map(|r| r.next_frame(buf)), Some(Ok(true)))
                }
            },
            None => false,
        }
    }
}

/// Dominant channel of a tightly-packed RGBA frame: 0=R,1=G,2=B.
fn dominant_channel(buf: &[u8]) -> usize {
    let (mut r, mut g, mut b) = (0u64, 0u64, 0u64);
    for px in buf.chunks_exact(4) {
        r += px[0] as u64;
        g += px[1] as u64;
        b += px[2] as u64;
    }
    if r >= g && r >= b {
        0
    } else if g >= r && g >= b {
        1
    } else {
        2
    }
}

/// Headless check: two solid-colour clips (red, blue) + an alternating timeline
/// must yield frames that switch colour cut-to-cut — proving clip selection and
/// per-cut decoding work.
pub fn selftest() -> Result<String, String> {
    use crate::media::generate_color_clip;
    use crate::timeline::build_timeline;

    let ffmpeg = ffmpeg_path();
    let red = std::env::temp_dir().join("am2_montage_red.mp4");
    let blue = std::env::temp_dir().join("am2_montage_blue.mp4");
    generate_color_clip(&ffmpeg, &red, "red", 64, 48, 1.0)?;
    generate_color_clip(&ffmpeg, &blue, "blue", 64, 48, 1.0)?;

    let pool = ClipPool::from_paths(vec![red.clone(), blue.clone()]);
    // uniform 120 BPM beats over 2 s → 4 cuts, clip idx alternates (no repeat)
    let beats: Vec<f32> = (0..4).map(|k| k as f32 * 0.5).collect();
    let cuts = build_timeline(&beats, 2.0, 1.0, 2, 1);
    let n = cuts.len();

    let mut mfs = MontageFrameSource::new(pool, cuts.clone());
    let mut buf = Vec::new();
    let mut colors = Vec::new();
    for c in &cuts {
        if mfs.frame_at(c.t_start + 0.01, &mut buf) {
            colors.push(dominant_channel(&buf));
        } else {
            colors.push(99);
        }
    }
    let _ = std::fs::remove_file(&red);
    let _ = std::fs::remove_file(&blue);

    // every adjacent pair of cuts must differ (the montage actually switched)
    let switched = colors.windows(2).all(|w| w[0] != w[1]);
    let valid = colors.iter().all(|&c| c == 0 || c == 2); // red or blue only
    if n >= 3 && switched && valid {
        Ok(format!("{n} cuts, colors {colors:?} alternate (clip switching works)"))
    } else {
        Err(format!("cuts={n}, colors={colors:?}, switched={switched}, valid={valid}"))
    }
}
