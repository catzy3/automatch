//! Timeline — turn a beat grid into a sequence of cuts with clip assignments.
//! Deterministic given a seed (preview == render), porting v1's build_timeline.

#![allow(dead_code)]

#[derive(Clone, Debug)]
pub struct Cut {
    pub t_start: f32,
    pub t_end: f32,
    pub clip_idx: usize,
}

/// xorshift64 — small deterministic RNG (no external crate).
fn next_rand(state: &mut u64) -> u64 {
    let mut x = *state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *state = x;
    x
}

/// Build cuts covering [0, duration). `cuts_per_beat` >= 1 subdivides each beat
/// interval; < 1 takes every Nth beat. Clip indices avoid immediate repeats.
pub fn build_timeline(
    beats: &[f32],
    duration: f32,
    cuts_per_beat: f32,
    n_clips: usize,
    seed: u64,
) -> Vec<Cut> {
    let mut starts: Vec<f32> = Vec::new();

    if beats.is_empty() {
        let step = 0.5;
        let mut t = 0.0;
        while t < duration {
            starts.push(t);
            t += step;
        }
    } else if cuts_per_beat >= 1.0 {
        let mult = cuts_per_beat.round().max(1.0) as usize;
        for w in beats.windows(2) {
            let (a, b) = (w[0], w[1]);
            for k in 0..mult {
                starts.push(a + (b - a) * k as f32 / mult as f32);
            }
        }
        if let Some(&last) = beats.last() {
            starts.push(last);
        }
    } else {
        let stride = (1.0 / cuts_per_beat).round().max(1.0) as usize;
        for (i, &b) in beats.iter().enumerate() {
            if i % stride == 0 {
                starts.push(b);
            }
        }
    }

    starts.retain(|&t| t < duration && t >= 0.0);
    if starts.is_empty() {
        starts.push(0.0);
    }
    starts.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if starts[0] > 1e-4 {
        starts.insert(0, 0.0);
    }
    starts.dedup_by(|a, b| (*a - *b).abs() < 1e-4);

    let mut state = seed.max(1);
    let mut cuts = Vec::with_capacity(starts.len());
    let mut prev_clip = usize::MAX;
    for i in 0..starts.len() {
        let t_start = starts[i];
        let t_end = if i + 1 < starts.len() {
            starts[i + 1]
        } else {
            duration
        };
        let clip = if n_clips == 0 {
            0
        } else {
            let mut c = (next_rand(&mut state) % n_clips as u64) as usize;
            if n_clips > 1 && c == prev_clip {
                c = (c + 1) % n_clips;
            }
            c
        };
        prev_clip = clip;
        cuts.push(Cut { t_start, t_end, clip_idx: clip });
    }
    cuts
}

/// Headless check: uniform 120 BPM beats → timeline with monotonic,
/// in-range, no-immediate-repeat cuts ending exactly at duration.
pub fn selftest() -> Result<String, String> {
    let duration = 6.0f32;
    let mut beats = Vec::new();
    let mut t = 0.0;
    while t < duration {
        beats.push(t);
        t += 0.5; // 120 BPM
    }
    let n_clips = 4;
    let cuts = build_timeline(&beats, duration, 1.0, n_clips, 42);

    if cuts.is_empty() {
        return Err("no cuts".into());
    }
    // monotonic, contiguous, in-range clips, no immediate repeats
    let mut prev_end = 0.0f32;
    let mut prev_clip = usize::MAX;
    for c in &cuts {
        if c.t_start < prev_end - 1e-3 || c.t_end <= c.t_start {
            return Err(format!("non-monotonic cut {c:?}"));
        }
        if c.clip_idx >= n_clips {
            return Err(format!("clip idx out of range: {}", c.clip_idx));
        }
        if c.clip_idx == prev_clip {
            return Err(format!("immediate repeat clip {}", c.clip_idx));
        }
        prev_end = c.t_end;
        prev_clip = c.clip_idx;
    }
    let last_end = cuts.last().unwrap().t_end;
    if (last_end - duration).abs() > 1e-3 {
        return Err(format!("last cut ends at {last_end}, want {duration}"));
    }
    // determinism: same seed → same clip sequence
    let cuts2 = build_timeline(&beats, duration, 1.0, n_clips, 42);
    let same = cuts.len() == cuts2.len()
        && cuts.iter().zip(&cuts2).all(|(a, b)| a.clip_idx == b.clip_idx);
    if !same {
        return Err("non-deterministic for same seed".into());
    }
    Ok(format!("cuts={}, deterministic, ends@{:.1}s", cuts.len(), last_end))
}
