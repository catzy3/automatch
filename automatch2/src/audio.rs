//! Audio analysis — decode to mono PCM and estimate tempo + beat times.
//!
//! Phase 4 uses an energy-based onset envelope + autocorrelation tempo
//! estimate, then a phase-aligned uniform beat grid. This is robust for
//! percussive material; a spectral-flux / aubio upgrade for full music is a
//! later refinement (the timeline only needs a solid BPM + phase).

#![allow(dead_code)]

use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};

pub const SR: u32 = 22_050;
pub const HOP: usize = 512;

pub struct AudioAnalysis {
    pub bpm: f32,
    pub beats: Vec<f32>,
    pub duration: f32,
}

/// Decode an audio/video file to mono f32 PCM at `SR` via ffmpeg.
pub fn decode_pcm(ffmpeg: &Path, file: &Path) -> Result<Vec<f32>, String> {
    let mut child = Command::new(ffmpeg)
        .args(["-hide_banner", "-loglevel", "error", "-i"])
        .arg(file)
        .args(["-f", "f32le", "-ac", "1", "-ar", &SR.to_string(), "pipe:1"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("spawn ffmpeg failed: {e}"))?;
    let mut bytes = Vec::new();
    child
        .stdout
        .take()
        .ok_or("no ffmpeg stdout")?
        .read_to_end(&mut bytes)
        .map_err(|e| format!("read pcm failed: {e}"))?;
    let _ = child.wait();
    Ok(bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect())
}

/// Half-wave-rectified energy difference per hop — a cheap onset envelope.
pub fn onset_envelope(pcm: &[f32]) -> Vec<f32> {
    if pcm.is_empty() {
        return Vec::new();
    }
    let n = pcm.len() / HOP;
    let mut energy = Vec::with_capacity(n);
    for i in 0..n {
        let s = &pcm[i * HOP..(i + 1) * HOP];
        let e: f32 = s.iter().map(|x| x * x).sum::<f32>() / HOP as f32;
        energy.push(e.sqrt());
    }
    let mut env = Vec::with_capacity(n);
    let mut prev = 0.0;
    for &e in &energy {
        env.push((e - prev).max(0.0));
        prev = e;
    }
    // normalize
    let max = env.iter().cloned().fold(0.0f32, f32::max);
    if max > 1e-9 {
        for v in &mut env {
            *v /= max;
        }
    }
    env
}

/// Autocorrelation of the onset envelope at a given lag (in frames).
fn autocorr(env: &[f32], lag: usize) -> f32 {
    if lag == 0 || lag >= env.len() {
        return 0.0;
    }
    let mut ac = 0.0f32;
    for i in lag..env.len() {
        ac += env[i] * env[i - lag];
    }
    ac
}

/// Best autocorrelation in a small lag neighborhood (handles non-integer beat
/// periods that drift across frames), returning (lag, value).
fn best_in_neighborhood(env: &[f32], center: usize, radius: usize) -> (usize, f32) {
    let lo = center.saturating_sub(radius).max(1);
    let hi = (center + radius).min(env.len() - 1);
    let mut bl = lo;
    let mut bv = f32::MIN;
    for lag in lo..=hi {
        let v = autocorr(env, lag);
        if v > bv {
            bv = v;
            bl = lag;
        }
    }
    (bl, bv)
}

/// Autocorrelation tempo estimate over a musical BPM range, with octave
/// correction toward the faster tempo (resolves the common half-tempo error).
pub fn estimate_bpm(env: &[f32]) -> f32 {
    if env.len() < 8 {
        return 120.0;
    }
    // Smooth the envelope so a non-integer beat period still autocorrelates
    // cleanly at integer lags (sharp clicks otherwise drift between frames).
    let mut sm = vec![0.0f32; env.len()];
    let r = 2i32;
    for i in 0..env.len() {
        let mut s = 0.0;
        let mut c = 0.0;
        for d in -r..=r {
            let j = i as i32 + d;
            if j >= 0 && (j as usize) < env.len() {
                s += env[j as usize];
                c += 1.0;
            }
        }
        sm[i] = s / c;
    }

    let fps = SR as f32 / HOP as f32; // envelope frames per second
    let (min_bpm, max_bpm) = (60.0f32, 200.0f32);
    let lag_min = ((60.0 * fps / max_bpm).round() as usize).max(1);
    let lag_max = ((60.0 * fps / min_bpm).round() as usize).min(sm.len() - 1).max(lag_min + 1);

    let mut best_lag = lag_min;
    let mut best = f32::MIN;
    for lag in lag_min..=lag_max {
        let ac = autocorr(&sm, lag);
        if ac > best {
            best = ac;
            best_lag = lag;
        }
    }

    // Octave correction: if a lag ≈ best/div (≈ ×div tempo) still correlates
    // strongly, prefer the faster tempo (autocorr peaks at every integer
    // multiple of the true period). Search ±1 around the candidate.
    for div in [2usize, 3] {
        let cand = best_lag / div;
        if cand >= lag_min {
            let (cl, cv) = best_in_neighborhood(&sm, cand, 1);
            if cv >= 0.55 * best {
                best_lag = cl;
                best = autocorr(&sm, best_lag);
            }
        }
    }

    60.0 * fps / best_lag as f32
}

/// Phase-align a uniform beat grid of the given BPM to the onset envelope.
pub fn beat_times(env: &[f32], bpm: f32, duration: f32) -> Vec<f32> {
    if bpm <= 1.0 {
        return Vec::new();
    }
    let fps = SR as f32 / HOP as f32;
    let period_frames = 60.0 / bpm * fps;
    if period_frames < 1.0 || env.is_empty() {
        return Vec::new();
    }
    // best phase offset = grid alignment maximizing summed onset energy
    let steps = period_frames.round().max(1.0) as usize;
    let mut best_off = 0usize;
    let mut best = f32::MIN;
    for off in 0..steps {
        let mut s = 0.0f32;
        let mut f = off as f32;
        while (f as usize) < env.len() {
            s += env[f as usize];
            f += period_frames;
        }
        if s > best {
            best = s;
            best_off = off;
        }
    }
    let period = 60.0 / bpm;
    let phase = best_off as f32 / fps;
    let mut beats = Vec::new();
    let mut t = phase;
    while t < duration {
        beats.push(t);
        t += period;
    }
    beats
}

/// Full pipeline: decode → onset → bpm → beats.
pub fn analyze(ffmpeg: &Path, file: &Path) -> Result<AudioAnalysis, String> {
    let pcm = decode_pcm(ffmpeg, file)?;
    let duration = pcm.len() as f32 / SR as f32;
    let env = onset_envelope(&pcm);
    let bpm = estimate_bpm(&env);
    let beats = beat_times(&env, bpm, duration);
    Ok(AudioAnalysis { bpm, beats, duration })
}

/// Headless check of the FILE path: ffmpeg generates a rhythmic 120 BPM click
/// WAV, then `decode_pcm` + `analyze` recover the tempo. Exercises the real
/// decode pipeline (unlike `selftest`, which uses in-memory PCM).
pub fn selftest_file() -> Result<String, String> {
    let ffmpeg = crate::media::ffmpeg_path();
    let tmp = std::env::temp_dir().join("am2_audio_selftest.wav");
    // 1 kHz tone decaying every 0.5 s → clicks at 120 BPM.
    // (t - 0.5*floor(t/0.5)) is `t mod 0.5` without a comma in the expr.
    let expr = "sin(6283.185*t)*exp(-8*(t-0.5*floor(t/0.5)))";
    let src = format!("aevalsrc={expr}:s={SR}:d=6");
    let st = Command::new(&ffmpeg)
        .args(["-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i"])
        .arg(&src)
        .arg(&tmp)
        .status()
        .map_err(|e| format!("gen click wav: {e}"))?;
    if !st.success() || !tmp.exists() {
        return Err("click wav generation failed".into());
    }
    let res = analyze(&ffmpeg, &tmp);
    let _ = std::fs::remove_file(&tmp);
    let a = res?;
    let ok = (a.bpm - 120.0).abs() <= 6.0 && a.beats.len() >= 10 && a.duration >= 5.0;
    let msg = format!("file decode → bpm={:.1}, beats={}, {:.1}s", a.bpm, a.beats.len(), a.duration);
    if ok {
        Ok(msg)
    } else {
        Err(msg)
    }
}

/// Headless check: synthesize a 120 BPM click track and confirm detection.
pub fn selftest() -> Result<String, String> {
    let bpm_true = 120.0f32;
    let dur = 6.0f32;
    let period = (60.0 / bpm_true * SR as f32) as usize;
    let total = (dur * SR as f32) as usize;
    let mut pcm = vec![0.0f32; total];
    // place a short decaying 1 kHz burst on each beat
    let mut t = 0usize;
    while t < total {
        for k in 0..(SR as usize / 100) {
            // 10 ms
            if t + k >= total {
                break;
            }
            let env = (-(k as f32) / (SR as f32 * 0.004)).exp();
            let s = (2.0 * std::f32::consts::PI * 1000.0 * k as f32 / SR as f32).sin();
            pcm[t + k] += env * s;
        }
        t += period;
    }
    let env = onset_envelope(&pcm);
    let bpm = estimate_bpm(&env);
    let beats = beat_times(&env, bpm, dur);
    let ok = (bpm - bpm_true).abs() <= 4.0 && beats.len() >= 10;
    let msg = format!("bpm={bpm:.1} (true 120), beats={}", beats.len());
    if ok {
        Ok(msg)
    } else {
        Err(msg)
    }
}
