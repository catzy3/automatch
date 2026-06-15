//! Montage engine — the auto-editor core: a pool of clips driven by the
//! beat-sync timeline, switching clips at each cut. This is what turns the
//! tool from an effects playground into an actual auto-editor.
//!
//! Phase: a frame source that, for a given playback time, decodes from the
//! clip the timeline assigned to the active cut, switching readers on cut
//! boundaries (clips loop if shorter than their cut). Random in-points,
//! transitions and prefetch are later refinements.

#![allow(dead_code)]

use crate::media::{ffmpeg_path, probe_dimensions, probe_duration, VideoReader};
use crate::timeline::Cut;
use std::path::{Path, PathBuf};

const VIDEO_EXTS: &[&str] = &["mp4", "mov", "mkv", "webm", "avi", "m4v"];

/// xorshift64 — deterministic RNG for in-point assignment (preview == render).
fn next_rand(state: &mut u64) -> u64 {
    let mut x = *state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *state = x;
    x
}

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
    canvas: (i32, i32),   // uniform output size every clip is scaled to
    clip_durs: Vec<f32>,  // probed duration (s) per pool path; 0 = unknown
    cur_cut: usize,
    reader: Option<VideoReader>,
    fade: f32,            // crossfade duration at each cut (seconds)
    prev_last: Vec<u8>,   // last frame of the previous cut (for the dissolve)
    fade_start: f32,      // t_start of the cut being faded into
}

impl MontageFrameSource {
    pub fn new(pool: ClipPool, mut cuts: Vec<Cut>) -> Self {
        let ffmpeg = ffmpeg_path();
        // canvas = first clip's native size (even dims), else a sane default
        let canvas = pool
            .paths
            .first()
            .and_then(|p| probe_dimensions(&ffmpeg, p).ok())
            .map(|(w, h)| (w / 2 * 2, h / 2 * 2))
            .unwrap_or((1280, 720));
        // probe each clip's length so we can pick safe random in-points
        let clip_durs: Vec<f32> = pool
            .paths
            .iter()
            .map(|p| probe_duration(&ffmpeg, p).unwrap_or(0.0))
            .collect();
        assign_inpoints(&mut cuts, &clip_durs, pool.len());
        Self {
            ffmpeg,
            pool,
            cuts,
            canvas,
            clip_durs,
            cur_cut: usize::MAX,
            reader: None,
            fade: 0.15,
            prev_last: Vec::new(),
            fade_start: 0.0,
        }
    }

    /// Set the crossfade duration at cuts (0 = hard cuts).
    pub fn set_fade(&mut self, fade: f32) {
        self.fade = fade.max(0.0);
    }

    pub fn canvas(&self) -> (i32, i32) {
        self.canvas
    }

    pub fn dimensions(&self) -> Option<(i32, i32)> {
        Some(self.canvas)
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
        let (w, h) = self.canvas;
        let src_in = self.cuts[ci].src_in;
        self.reader =
            VideoReader::open_scaled_at(&self.ffmpeg, &self.pool.paths[clip_idx], w, h, src_in).ok();
    }

    /// Reopen the current cut's clip from its start — used when a clip runs out
    /// of frames inside its cut (it was shorter than the cut, or the in-point
    /// left too little footage), so the loop replays the full clip.
    fn reopen_loop(&mut self, ci: usize) {
        self.reader = None;
        if self.pool.is_empty() || ci >= self.cuts.len() {
            return;
        }
        let clip_idx = self.cuts[ci].clip_idx % self.pool.len();
        let (w, h) = self.canvas;
        self.reader =
            VideoReader::open_scaled(&self.ffmpeg, &self.pool.paths[clip_idx], w, h).ok();
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
            // entering a new cut: `buf` still holds the previous cut's last
            // frame → snapshot it for the crossfade (no per-frame cloning).
            if self.cur_cut != usize::MAX && self.fade > 0.0 && !buf.is_empty() {
                self.prev_last.clear();
                self.prev_last.extend_from_slice(buf);
                self.fade_start = self.cuts[ci].t_start;
            }
            self.open_cut(ci);
        }
        let got = match self.reader.as_mut() {
            Some(r) => match r.next_frame(buf) {
                Ok(true) => true,
                _ => {
                    self.reopen_loop(ci); // clip ended inside the cut → loop from start
                    matches!(self.reader.as_mut().map(|r| r.next_frame(buf)), Some(Ok(true)))
                }
            },
            None => false,
        };
        if !got {
            return false;
        }
        // ── crossfade from the previous cut over `fade` seconds ──
        if self.fade > 0.0 && self.prev_last.len() == buf.len() {
            let k = ((t - self.fade_start) / self.fade).clamp(0.0, 1.0);
            if k < 1.0 {
                let ik = 1.0 - k;
                for (b, p) in buf.iter_mut().zip(self.prev_last.iter()) {
                    *b = (*b as f32 * k + *p as f32 * ik) as u8;
                }
            } else {
                self.prev_last.clear();
            }
        }
        true
    }
}

/// Assign a deterministic random in-point to every cut. The offset is chosen in
/// `[0, dur - cut_len - tail]` so the clip has enough footage to cover the whole
/// cut without looping; clips too short for their cut start at 0. Deterministic
/// in the cut list → preview and render pick identical in-points.
fn assign_inpoints(cuts: &mut [Cut], clip_durs: &[f32], n_clips: usize) {
    const TAIL: f32 = 0.05; // keep a little margin before EOF
    let mut state: u64 =
        0x9E37_79B9_7F4A_7C15 ^ (cuts.len() as u64).wrapping_mul(0x2545_F491_4F6C_DD1D);
    for c in cuts.iter_mut() {
        let r = next_rand(&mut state); // advance every cut (branch-independent)
        let pi = if n_clips == 0 { 0 } else { c.clip_idx % n_clips };
        let dur = clip_durs.get(pi).copied().unwrap_or(0.0);
        let cut_len = (c.t_end - c.t_start).max(0.0);
        let headroom = dur - cut_len - TAIL;
        c.src_in = if headroom > 0.10 {
            let frac = (r >> 11) as f32 / (1u64 << 53) as f32; // 53-bit → [0,1)
            frac * headroom
        } else {
            0.0
        };
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
    use crate::media::{generate_color_clip, generate_test_clip};
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

    // ── part 1: clip switching (sample late in each cut, past the fade) ──
    let mut mfs = MontageFrameSource::new(pool.clone(), cuts.clone());
    let mut buf = Vec::new();
    let mut colors = Vec::new();
    for c in &cuts {
        if mfs.frame_at((c.t_end - 0.02).max(c.t_start), &mut buf) {
            colors.push(dominant_channel(&buf));
        } else {
            colors.push(99);
        }
    }
    let switched = colors.windows(2).all(|w| w[0] != w[1]);
    let valid = colors.iter().all(|&c| c == 0 || c == 2); // red or blue only

    // ── part 2: crossfade — mid-transition must blend both clips ──
    let mut mfs2 = MontageFrameSource::new(pool, cuts.clone());
    let _ = mfs2.frame_at((cuts[0].t_end - 0.02).max(0.0), &mut buf); // establish cut 0
    mfs2.frame_at(cuts[1].t_start + 0.075, &mut buf); // ~mid of a 0.15s fade
    let (r, _g, b) = avg_rgb(&buf);
    let blended = r > 60 && b > 60; // both red & blue present → dissolve

    // ── part 3: random in-point seek — different start offsets on an animated
    //    clip must yield different frames (proves open_scaled_at seeks) ──
    let anim = std::env::temp_dir().join("am2_montage_anim.mp4");
    generate_test_clip(&ffmpeg, &anim, 64, 48, 2.0)?;
    let mut f0 = Vec::new();
    let mut f1 = Vec::new();
    let seeked = {
        let mut r0 = VideoReader::open_scaled_at(&ffmpeg, &anim, 64, 48, 0.0).ok();
        let mut r1 = VideoReader::open_scaled_at(&ffmpeg, &anim, 64, 48, 1.0).ok();
        let g0 = matches!(r0.as_mut().map(|r| r.next_frame(&mut f0)), Some(Ok(true)));
        let g1 = matches!(r1.as_mut().map(|r| r.next_frame(&mut f1)), Some(Ok(true)));
        g0 && g1 && f0.len() == f1.len() && f0 != f1
    };

    let _ = std::fs::remove_file(&red);
    let _ = std::fs::remove_file(&blue);
    let _ = std::fs::remove_file(&anim);

    if n >= 3 && switched && valid && blended && seeked {
        Ok(format!("{n} cuts {colors:?} alternate; crossfade blends (R={r},B={b}); in-point seek differs"))
    } else {
        Err(format!("cuts={n}, colors={colors:?}, switched={switched}, valid={valid}, blended={blended} (R={r},B={b}), seeked={seeked}"))
    }
}

fn avg_rgb(buf: &[u8]) -> (i32, i32, i32) {
    if buf.len() < 4 {
        return (0, 0, 0);
    }
    let (mut r, mut g, mut b, mut n) = (0u64, 0u64, 0u64, 0u64);
    for px in buf.chunks_exact(4) {
        r += px[0] as u64;
        g += px[1] as u64;
        b += px[2] as u64;
        n += 1;
    }
    n = n.max(1);
    ((r / n) as i32, (g / n) as i32, (b / n) as i32)
}
