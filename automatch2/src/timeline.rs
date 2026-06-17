//! Timeline — turn a beat grid into a sequence of cuts with clip assignments.
//! Deterministic given a seed (preview == render), porting v1's build_timeline.

#![allow(dead_code)]

#[derive(Clone, Debug)]
pub struct Cut {
    pub t_start: f32,
    pub t_end: f32,
    pub clip_idx: usize,
    /// Seconds into the source clip at which this cut begins decoding. 0 = clip
    /// start. Assigned by the montage layer (it knows clip durations); lets the
    /// same clip show different footage each time it is reused.
    pub src_in: f32,
}

/// xorshift64 — small deterministic RNG (no external crate).
pub(crate) fn next_rand(state: &mut u64) -> u64 {
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
        cuts.push(Cut { t_start, t_end, clip_idx: clip, src_in: 0.0 });
    }
    cuts
}

/// Post-process a cut list to add **stutter** (rapid-fire sub-cuts) and **hold**
/// (extended cuts that skip a clip-switch).
///
/// * `stutter_frac` — fraction of cuts subdivided into 2–4 rapid ping-pong sub-cuts [0,1]
/// * `hold_frac`    — fraction of cuts merged with the next cut (same clip, longer) [0,1]
///
/// Hold is evaluated first per cut; if not held, stutter is evaluated. Both are
/// deterministic for a given `seed`. Timeline duration is always preserved.
pub fn apply_cut_variation(
    cuts: &[Cut],
    stutter_frac: f32,
    hold_frac: f32,
    n_clips: usize,
    seed: u64,
) -> Vec<Cut> {
    if cuts.is_empty() || (stutter_frac <= 0.0 && hold_frac <= 0.0) {
        return cuts.to_vec();
    }
    let mut state = seed.max(1) ^ 0xDEAD_BEEF_1234_5678;
    let mut out: Vec<Cut> = Vec::with_capacity(cuts.len() + 8);
    let mut i = 0;
    while i < cuts.len() {
        let c = &cuts[i];
        let r  = (next_rand(&mut state) >> 11) as f32 / (1u64 << 53) as f32;
        let r2 = (next_rand(&mut state) >> 11) as f32 / (1u64 << 53) as f32;

        // hold: merge this cut with the next — same clip, double duration
        if r < hold_frac && i + 1 < cuts.len() {
            out.push(Cut {
                t_start: c.t_start,
                t_end: cuts[i + 1].t_end,
                clip_idx: c.clip_idx,
                src_in: c.src_in,
            });
            i += 2;
            continue;
        }

        // stutter: subdivide into 2 or 4 rapid ping-pong sub-cuts
        if r2 < stutter_frac {
            let n_sub: usize = if r2 < stutter_frac * 0.25 { 4 } else { 2 };
            let dur = c.t_end - c.t_start;
            let sub_dur = dur / n_sub as f32;
            // alternate between current clip and the previous one for ping-pong feel
            let alt_clip = if i > 0 {
                cuts[i - 1].clip_idx
            } else {
                (c.clip_idx + 1) % n_clips.max(2)
            };
            for s in 0..n_sub {
                let clip_idx = if s % 2 == 0 { c.clip_idx } else { alt_clip };
                out.push(Cut {
                    t_start: c.t_start + s as f32 * sub_dur,
                    t_end:   c.t_start + (s + 1) as f32 * sub_dur,
                    clip_idx,
                    src_in: c.src_in, // all sub-cuts share the in-point: stutters on the same frame
                });
            }
            i += 1;
            continue;
        }

        out.push(c.clone());
        i += 1;
    }
    out
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

/// Verify that apply_cut_variation increases cuts on stutter, decreases on hold,
/// and always preserves the total timeline duration.
pub fn selftest_variation() -> Result<String, String> {
    let duration = 4.0f32;
    let beats: Vec<f32> = (0..8).map(|k| k as f32 * 0.5).collect();
    let base = build_timeline(&beats, duration, 1.0, 4, 42);
    let n = base.len();

    // stutter: more cuts
    let stuttered = apply_cut_variation(&base, 0.6, 0.0, 4, 99);
    if stuttered.len() <= n {
        return Err(format!("stutter: expected >{n} cuts, got {}", stuttered.len()));
    }
    // hold: fewer cuts
    let held = apply_cut_variation(&base, 0.0, 0.6, 4, 99);
    if held.len() >= n {
        return Err(format!("hold: expected <{n} cuts, got {}", held.len()));
    }
    // duration preserved for both
    let end_s = stuttered.last().unwrap().t_end;
    let end_h = held.last().unwrap().t_end;
    if (end_s - duration).abs() > 1e-2 {
        return Err(format!("stutter: last end={end_s:.3}, want {duration}"));
    }
    if (end_h - duration).abs() > 1e-2 {
        return Err(format!("hold: last end={end_h:.3}, want {duration}"));
    }
    // monotonic
    for seq in [&stuttered, &held] {
        for w in seq.windows(2) {
            if w[0].t_end > w[1].t_start + 1e-3 {
                return Err(format!("non-monotonic: {:?} → {:?}", w[0], w[1]));
            }
        }
    }
    Ok(format!("base={n} → stutter={} hold={}", stuttered.len(), held.len()))
}
