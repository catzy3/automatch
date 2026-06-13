"""Real codec-level datamosh (Avidemux-style) — operates on the encoded
bitstream, NOT on pixels, so it produces the authentic compression artefact
the GPU `datamosh` shader can only fake.

Two artefacts (independently toggleable):
  bloom — remove I-frames (keyframes). The decoder keeps applying the previous
          frame's motion vectors onto whatever is on screen, so a hard cut
          "melts" into the next clip. The classic datamosh transition.
  smear — duplicate P-frames. The same motion delta is applied several times in
          a row, smearing / freezing movement. The "stuck" datamosh look.

Pipeline:
  mp4  --ffmpeg-->  MPEG-4 ASP AVI (I/P frames, no B)  -->  byte-edit the
  'movi' frame chunks  --ffmpeg-->  playable H.264 mp4 (silent)

The only external dependency is ffmpeg, which automatch already requires. The
AVI surgery is pure-Python byte manipulation (no numpy/cv2 needed here).

Frame add/remove changes the frame count, so the final re-encode rescales PTS
to land back on the requested duration — keeps the montage in sync with music.
"""

from __future__ import annotations

import random
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Optional


def _ffmpeg() -> Optional[str]:
    try:
        from imageio_ffmpeg import get_ffmpeg_exe  # type: ignore
        return get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


# ── MPEG-4 ASP VOP coding type ────────────────────────────────────────────
# A video frame's payload contains a VOP start code 0x000001B6; the two bits
# immediately after it are vop_coding_type: 0=I, 1=P, 2=B, 3=S.
_VOP_START = b"\x00\x00\x01\xb6"


def _frame_type(payload: bytes) -> int:
    """Return VOP coding type (0=I,1=P,2=B,3=S); -1 if no VOP found.

    Searches for the VOP start code because the first frame is preceded by
    VOS/VO/VOL configuration headers (0x000001B0 / B5 / 20…).
    """
    idx = payload.find(_VOP_START)
    if idx < 0 or idx + 4 >= len(payload):
        return -1
    return (payload[idx + 4] >> 6) & 0x03


def _find_movi(data: bytes) -> Optional[tuple]:
    """Locate the 'movi' LIST. Returns (list_size_pos, movi_fourcc_pos,
    movi_data_start, movi_data_end) or None.
    """
    p = data.find(b"movi")
    while p != -1:
        # a real movi list is 'LIST' <size:4> 'movi'; verify the 'LIST' tag
        if p >= 8 and data[p - 8:p - 4] == b"LIST":
            size = struct.unpack_from("<I", data, p - 4)[0]
            data_start = p + 4
            data_end = (p - 4) + 4 + size  # size covers 'movi' + payload
            if data_end <= len(data):
                return (p - 4, p, data_start, data_end)
        p = data.find(b"movi", p + 4)
    return None


def _parse_frames(data: bytes, start: int, end: int) -> Optional[list]:
    """Walk the chunks inside the movi payload.

    Returns an ordered list of dicts {fourcc, payload, type} for video frames
    (fourcc ending in 'dc'/'db'). Returns None on a malformed structure.
    """
    frames = []
    off = start
    while off + 8 <= end:
        fourcc = data[off:off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        body = data[off + 8:off + 8 + size]
        if off + 8 + size > end + 2:        # size runs past movi → corrupt
            return None
        # video stream chunks: '00dc' (compressed) or '00db' (uncompressed DIB)
        if fourcc[2:4] in (b"dc", b"db"):
            frames.append({
                "fourcc": fourcc,
                "payload": body,
                "type": _frame_type(body),
            })
        # 'LIST'/'rec '/'JUNK'/audio chunks: skip (video-only AVI shouldn't
        # have them, but stay defensive). advance over them too.
        off += 8 + size + (size & 1)        # chunks are word-aligned
    return frames if frames else None


def _apply_mosh(frames: list, *, bloom: bool, smear: bool,
                bloom_chance: float, smear_repeat: int, smear_chance: float,
                seed: int) -> list:
    """Transform the ordered frame list. Always keeps the first frame (the
    seed I-frame carrying the VOL config headers)."""
    rng = random.Random(seed or 1)
    out = []
    for i, fr in enumerate(frames):
        is_key = fr["type"] == 0
        # ── bloom: drop I-frames (except the very first frame) ──
        if bloom and is_key and i > 0:
            if rng.random() < bloom_chance:
                continue                    # melt: skip this keyframe entirely
        out.append(fr)
        # ── smear: duplicate P-frames so motion repeats ──
        if smear and not is_key and i > 0 and smear_repeat > 0:
            if rng.random() < smear_chance:
                for _ in range(smear_repeat):
                    out.append(fr)
    # guarantee at least the seed frame survives
    return out or frames[:1]


def _rebuild_avi(data: bytes, movi: tuple, frames: list) -> bytes:
    """Reassemble the AVI: keep everything up to the 'movi' fourcc, append the
    rebuilt frame chunks, patch the RIFF + movi LIST sizes, drop the (now
    stale) idx1 index. ffmpeg re-decodes by scanning, so a missing index is
    fine for the immediate re-encode step.
    """
    list_size_pos, movi_pos, data_start, _data_end = movi
    prefix = bytearray(data[:data_start])   # … 'LIST' <size> 'movi'

    body = bytearray()
    for fr in frames:
        payload = fr["payload"]
        body += fr["fourcc"]
        body += struct.pack("<I", len(payload))
        body += payload
        if len(payload) & 1:
            body += b"\x00"                 # word-align

    out = prefix + body
    # movi LIST size = 'movi'(4) + body
    struct.pack_into("<I", out, list_size_pos, 4 + len(body))
    # RIFF size = total file size - 8
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out)


def _even(v: float) -> int:
    """Nearest even integer ≥ 2 (encoders need mod-2 dimensions)."""
    return max(2, int(round(v)) // 2 * 2)


def datamosh_video(in_path: Path, out_path: Path, cfg, *,
                   fps: float, duration: float, seed: int = 0,
                   crf: int = 18, preset: str = "medium",
                   out_w: int = 0, out_h: int = 0,
                   report=None) -> Optional[Path]:
    """Datamosh ``in_path`` → ``out_path`` (both silent video). Returns
    out_path on success, None on any failure (caller should fall back to the
    un-moshed video).

    ``cfg`` is any object exposing: bloom, smear, keyframe_sec, bloom_chance,
    smear_repeat, smear_chance, fine_scale (see DatamoshRenderSettings).

    ``fine_scale`` > 1 encodes the intermediate AVI at a larger resolution so
    the codec's fixed 16×16 macroblocks cover a smaller fraction of the frame
    → finer, less blocky datamosh. The final pass scales back to out_w×out_h.
    """
    def _log(msg):
        if report:
            try: report(msg)
            except Exception: pass
        print(f"[datamosh] {msg}")

    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        _log("ffmpeg not found — skipping datamosh")
        return None

    in_path = Path(in_path)
    out_path = Path(out_path)
    src_avi = out_path.with_suffix(".mosh_src.avi")
    moshed_avi = out_path.with_suffix(".mosh_edit.avi")

    bloom = bool(getattr(cfg, "bloom", True))
    smear = bool(getattr(cfg, "smear", False))
    if not (bloom or smear):
        return None

    gop = max(1, int(round(float(getattr(cfg, "keyframe_sec", 2.0)) * fps)))
    # finer blocks: upscale the intermediate, then scale back at the end
    fine_scale = max(1.0, min(4.0, float(getattr(cfg, "fine_scale", 1.0))))
    # lower q = finer detail / smoother gradients than the blocky default
    quality = max(1, min(8, int(getattr(cfg, "quality", 3))))
    do_scale = fine_scale > 1.001 and out_w > 0 and out_h > 0

    try:
        # 1) encode to MPEG-4 ASP AVI: I/P only (-bf 0), periodic keyframes
        #    plus scene-cut keyframes (default sc) so montage cuts get an
        #    I-frame to melt. Video-only (-an) for clean parsing.
        enc = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(in_path),
            "-an",
        ]
        if do_scale:
            enc += ["-vf", f"scale={_even(out_w * fine_scale)}:"
                           f"{_even(out_h * fine_scale)}:flags=bicubic"]
        enc += [
            "-c:v", "mpeg4", "-vtag", "xvid",
            "-q:v", str(quality),
            "-g", str(gop), "-bf", "0",
            str(src_avi),
        ]
        if subprocess.run(enc, capture_output=True).returncode != 0 \
                or not src_avi.exists():
            _log("intermediate AVI encode failed")
            return None

        # 2) byte-surgery on the AVI
        data = src_avi.read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"AVI ":
            _log("not a RIFF/AVI container")
            return None
        movi = _find_movi(data)
        if movi is None:
            _log("no 'movi' list found")
            return None
        frames = _parse_frames(data, movi[2], movi[3])
        if not frames:
            _log("no video frames parsed")
            return None
        n_in = len(frames)
        moshed = _apply_mosh(
            frames,
            bloom=bloom, smear=smear,
            bloom_chance=float(getattr(cfg, "bloom_chance", 1.0)),
            smear_repeat=int(getattr(cfg, "smear_repeat", 2)),
            smear_chance=float(getattr(cfg, "smear_chance", 0.25)),
            seed=seed,
        )
        n_out = len(moshed)
        kept_keys = sum(1 for f in moshed if f["type"] == 0)
        _log(f"frames {n_in} → {n_out} (I-frames kept: {kept_keys})")
        moshed_avi.write_bytes(_rebuild_avi(data, movi, moshed))

        # 3) re-encode to a playable, CFR mp4. Rescale PTS so the moshed clip
        #    lasts exactly `duration` → stays aligned with the music on mux.
        native_dur = n_out / float(fps) if fps > 0 else duration
        scale = (float(duration) / native_dur) if native_dur > 1e-6 else 1.0
        vf = f"setpts={scale:.6f}*PTS"
        if do_scale:                       # scale the finer intermediate back down
            vf += f",scale={int(out_w)}:{int(out_h)}:flags=bilinear"
        dec = [
            ffmpeg, "-y", "-v", "error",
            "-fflags", "+genpts",
            "-i", str(moshed_avi),
            "-vf", vf,
            "-r", str(fps),
            "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", preset, "-crf", str(crf),
            "-flags:v", "+bitexact", "-fflags", "+bitexact",
            str(out_path),
        ]
        if subprocess.run(dec, capture_output=True).returncode != 0 \
                or not out_path.exists():
            _log("final re-encode failed")
            return None
        return out_path
    except Exception as e:
        _log(f"error: {e}")
        return None
    finally:
        for tmp in (src_avi, moshed_avi):
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
