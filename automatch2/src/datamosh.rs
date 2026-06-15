//! Real codec-level datamosh (Avidemux-style), ported from v1's
//! `datamosh_bitstream.py`. Operates on the encoded bitstream, not pixels:
//!
//!   bloom — drop I-frames so cuts melt into each other.
//!   smear — duplicate P-frames so motion stutters/freezes.
//!
//! Pipeline: mp4 → (ffmpeg) MPEG-4 ASP AVI (I/P only) → byte-edit the 'movi'
//! frame chunks → (ffmpeg) back to a playable H.264 mp4. `fine_scale` encodes
//! the intermediate larger so the codec's 16×16 macroblocks look finer.

#![allow(dead_code)]

use crate::media;
use std::path::Path;
use std::process::Command;

#[derive(Clone, serde::Serialize, serde::Deserialize)]
pub struct DatamoshOpts {
    pub bloom: bool,
    pub smear: bool,
    pub keyframe_sec: f32,
    pub bloom_chance: f32,
    pub smear_repeat: u32,
    pub smear_chance: f32,
    pub fine_scale: f32,
    pub quality: u32,
}

impl Default for DatamoshOpts {
    fn default() -> Self {
        Self {
            bloom: true,
            smear: false,
            keyframe_sec: 2.0,
            bloom_chance: 1.0,
            smear_repeat: 1,
            smear_chance: 0.25,
            fine_scale: 2.0,
            quality: 3,
        }
    }
}

fn xorshift(state: &mut u64) -> u64 {
    let mut x = *state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *state = x;
    x
}

fn rand01(state: &mut u64) -> f32 {
    (xorshift(state) >> 40) as f32 / (1u64 << 24) as f32
}

fn read_u32(d: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([d[i], d[i + 1], d[i + 2], d[i + 3]])
}

fn find_subslice(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || hay.len() < needle.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

/// MPEG-4 ASP VOP coding type: 0=I,1=P,2=B,3=S; -1 if no VOP start code.
fn frame_type(payload: &[u8]) -> i32 {
    match find_subslice(payload, &[0x00, 0x00, 0x01, 0xB6]) {
        Some(idx) if idx + 4 < payload.len() => ((payload[idx + 4] >> 6) & 0x03) as i32,
        _ => -1,
    }
}

struct Movi {
    list_size_pos: usize,
    movi_data_start: usize,
    movi_data_end: usize,
}

fn find_movi(data: &[u8]) -> Option<Movi> {
    let mut search = 0usize;
    while let Some(rel) = find_subslice(&data[search..], b"movi") {
        let p = search + rel;
        if p >= 8 && &data[p - 8..p - 4] == b"LIST" {
            let size = read_u32(data, p - 4) as usize;
            let data_start = p + 4;
            let data_end = (p - 4) + 4 + size; // size covers 'movi' + payload
            if data_end <= data.len() {
                return Some(Movi {
                    list_size_pos: p - 4,
                    movi_data_start: data_start,
                    movi_data_end: data_end,
                });
            }
        }
        search = p + 4;
    }
    None
}

struct Frame {
    fourcc: [u8; 4],
    payload: Vec<u8>,
    vtype: i32,
}

fn parse_frames(data: &[u8], start: usize, end: usize) -> Vec<Frame> {
    let mut frames = Vec::new();
    let mut off = start;
    while off + 8 <= end {
        let fourcc = [data[off], data[off + 1], data[off + 2], data[off + 3]];
        let size = read_u32(data, off + 4) as usize;
        if off + 8 + size > end + 2 {
            break;
        }
        let payload = data[off + 8..(off + 8 + size).min(data.len())].to_vec();
        if &fourcc[2..4] == b"dc" || &fourcc[2..4] == b"db" {
            let vtype = frame_type(&payload);
            frames.push(Frame { fourcc, payload, vtype });
        }
        off += 8 + size + (size & 1); // word-align
    }
    frames
}

fn apply_mosh(frames: Vec<Frame>, o: &DatamoshOpts, seed: u64) -> Vec<Frame> {
    let mut state = seed.max(1);
    let mut out: Vec<Frame> = Vec::new();
    for (i, fr) in frames.into_iter().enumerate() {
        let is_key = fr.vtype == 0;
        if o.bloom && is_key && i > 0 && rand01(&mut state) < o.bloom_chance {
            continue; // drop keyframe → melt
        }
        let dup = o.smear && !is_key && i > 0 && o.smear_repeat > 0
            && rand01(&mut state) < o.smear_chance;
        let copies = if dup { o.smear_repeat } else { 0 };
        let payload = fr.payload.clone();
        let fourcc = fr.fourcc;
        let vtype = fr.vtype;
        out.push(fr);
        for _ in 0..copies {
            out.push(Frame { fourcc, payload: payload.clone(), vtype });
        }
    }
    if out.is_empty() {
        // nothing survived — keep at least a seed: rebuild a single black? No:
        // return empty signals caller to bail.
    }
    out
}

fn rebuild_avi(data: &[u8], movi: &Movi, frames: &[Frame]) -> Vec<u8> {
    let mut out = data[..movi.movi_data_start].to_vec();
    let mut body = Vec::new();
    for fr in frames {
        body.extend_from_slice(&fr.fourcc);
        body.extend_from_slice(&(fr.payload.len() as u32).to_le_bytes());
        body.extend_from_slice(&fr.payload);
        if fr.payload.len() & 1 == 1 {
            body.push(0);
        }
    }
    out.extend_from_slice(&body);
    // movi LIST size = 'movi'(4) + body
    let movi_size = (4 + body.len()) as u32;
    out[movi.list_size_pos..movi.list_size_pos + 4].copy_from_slice(&movi_size.to_le_bytes());
    // RIFF size = file - 8
    let riff_size = (out.len() - 8) as u32;
    out[4..8].copy_from_slice(&riff_size.to_le_bytes());
    out
}

fn even(v: f32) -> i32 {
    let n = v.round() as i32;
    (n / 2 * 2).max(2)
}

/// Datamosh `input` → `output` (both video files). Returns frames (in, out) on
/// success, or Err. `out_w/out_h` enable the fine-scale upscale/downscale.
pub fn datamosh_video(
    input: &Path,
    output: &Path,
    o: &DatamoshOpts,
    fps: f32,
    duration: f32,
    seed: u64,
    out_w: i32,
    out_h: i32,
) -> Result<(usize, usize), String> {
    if !o.bloom && !o.smear {
        return Err("nothing to do (bloom and smear both off)".into());
    }
    let ffmpeg = media::ffmpeg_path();
    let src_avi = output.with_extension("mosh_src.avi");
    let edit_avi = output.with_extension("mosh_edit.avi");

    let gop = ((o.keyframe_sec * fps).round() as i32).max(1);
    let fine = o.fine_scale.clamp(1.0, 4.0);
    let do_scale = fine > 1.001 && out_w > 0 && out_h > 0;

    // 1) mp4 → MPEG-4 ASP AVI (I/P only)
    let mut enc = Command::new(&ffmpeg);
    enc.args(["-y", "-hide_banner", "-loglevel", "error", "-i"]).arg(input).args(["-an"]);
    if do_scale {
        enc.args(["-vf", &format!("scale={}:{}:flags=bicubic", even(out_w as f32 * fine), even(out_h as f32 * fine))]);
    }
    enc.args(["-c:v", "mpeg4", "-vtag", "xvid", "-q:v", &o.quality.to_string(),
              "-g", &gop.to_string(), "-bf", "0"]).arg(&src_avi);
    let st = enc.status().map_err(|e| format!("encode avi: {e}"))?;
    if !st.success() || !src_avi.exists() {
        return Err("intermediate AVI encode failed".into());
    }

    // 2) byte surgery
    let result = (|| -> Result<(usize, usize), String> {
        let data = std::fs::read(&src_avi).map_err(|e| format!("read avi: {e}"))?;
        if data.len() < 12 || &data[0..4] != b"RIFF" || &data[8..12] != b"AVI " {
            return Err("not a RIFF/AVI".into());
        }
        let movi = find_movi(&data).ok_or("no movi list")?;
        let frames = parse_frames(&data, movi.movi_data_start, movi.movi_data_end);
        if frames.is_empty() {
            return Err("no video frames parsed".into());
        }
        let n_in = frames.len();
        let moshed = apply_mosh(frames, o, seed);
        if moshed.is_empty() {
            return Err("all frames removed".into());
        }
        let n_out = moshed.len();
        let rebuilt = rebuild_avi(&data, &movi, &moshed);
        std::fs::write(&edit_avi, &rebuilt).map_err(|e| format!("write avi: {e}"))?;

        // 3) AVI → playable mp4; rescale PTS so it lasts `duration`
        let native_dur = n_out as f32 / fps.max(1.0);
        let scale = if native_dur > 1e-6 { duration / native_dur } else { 1.0 };
        let mut vf = format!("setpts={scale:.6}*PTS");
        if do_scale {
            vf.push_str(&format!(",scale={out_w}:{out_h}:flags=bilinear"));
        }
        let st = Command::new(&ffmpeg)
            .args(["-y", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts", "-i"])
            .arg(&edit_avi)
            .args(["-vf", &vf, "-r", &fps.to_string(), "-an",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast"])
            .arg(output)
            .status()
            .map_err(|e| format!("final encode: {e}"))?;
        if !st.success() || !output.exists() {
            return Err("final re-encode failed".into());
        }
        Ok((n_in, n_out))
    })();

    let _ = std::fs::remove_file(&src_avi);
    let _ = std::fs::remove_file(&edit_avi);
    result
}

/// Headless check: generate a clip with cuts, datamosh it (bloom+smear), and
/// confirm the output decodes and the frame count changed (bytes were edited).
pub fn selftest() -> Result<String, String> {
    let ffmpeg = media::ffmpeg_path();
    let tmp_in = std::env::temp_dir().join("am2_mosh_in.mp4");
    let tmp_out = std::env::temp_dir().join("am2_mosh_out.mp4");
    media::generate_test_clip(&ffmpeg, &tmp_in, 160, 90, 2.0)?;

    let opts = DatamoshOpts {
        bloom: true,
        smear: true,
        keyframe_sec: 0.5,
        bloom_chance: 1.0,
        smear_repeat: 2,
        smear_chance: 0.5,
        fine_scale: 1.0, // keep selftest fast
        quality: 4,
    };
    let (n_in, n_out) =
        datamosh_video(&tmp_in, &tmp_out, &opts, 30.0, 2.0, 7, 160, 90)?;

    // verify the output is a real, decodable video
    let mut reader = media::VideoReader::open(&ffmpeg, &tmp_out)?;
    let (w, h) = (reader.w, reader.h);
    let mut buf = Vec::new();
    let mut got = 0u64;
    while reader.next_frame(&mut buf).unwrap_or(false) {
        got += 1;
        if got >= 5 {
            break;
        }
    }
    drop(reader);
    let _ = std::fs::remove_file(&tmp_in);
    let _ = std::fs::remove_file(&tmp_out);

    if w == 160 && h == 90 && got >= 5 && n_out != n_in {
        Ok(format!("frames {n_in}→{n_out}, output decodes {w}x{h}"))
    } else {
        Err(format!("n_in={n_in} n_out={n_out} decoded={got} {w}x{h}"))
    }
}
