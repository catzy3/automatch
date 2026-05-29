"""OpenGL render pipeline: dynamic chain of shader passes → preview + Spout + recorder queue.

Highlights:
  - Effects with their own previous-output texture (ping-pong feedback):
        datamosh, block_drift, glitch, crt, drip
  - light_bleed is a 2-pass effect: accumulate (writes bloom-only)
    + composite (overlays bloom on the current frame).
  - datamosh reads a per-pixel motion-vector texture produced by the
    capture thread (cv2 DIS optical flow).
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import cv2
import glfw
import moderngl
import numpy as np

from ascii_atlas import GLYPHS as ASCII_DEFAULT, load_atlas, make_atlas, make_atlas_array
from overlay import OverlayConfig, render_overlay
from state import SharedState

try:
    import SpoutGL  # type: ignore
    HAS_SPOUT = True
except ImportError:
    HAS_SPOUT = False
    print("[renderer] SpoutGL not installed — Spout output disabled. "
          "Install with: pip install SpoutGL")

SHADER_DIR = Path(__file__).parent / "shaders"


def _load(name: str) -> str:
    return (SHADER_DIR / name).read_text(encoding="utf-8")


QUAD_VERTS = np.array([
    -1.0, -1.0, 0.0, 0.0,
     1.0, -1.0, 1.0, 0.0,
    -1.0,  1.0, 0.0, 1.0,
     1.0,  1.0, 1.0, 1.0,
], dtype="f4")


# Effects that maintain a personal previous-output texture (ping-pong).
_EFFECTS_WITH_PREV = {"datamosh", "glitch", "crt", "drip",
                      "mirror_flow", "motion_trails", "quad_split",
                      "fragment_layers"}


def _make_fbo(ctx, w, h, components=4):
    tex = ctx.texture((w, h), components)
    tex.repeat_x = False
    tex.repeat_y = False
    tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
    fbo = ctx.framebuffer(color_attachments=[tex])
    return tex, fbo


class RenderThread(threading.Thread):
    def __init__(self, state: SharedState,
                 recorder_queue: queue.Queue | None = None) -> None:
        super().__init__(name="RenderThread", daemon=True)
        self.state = state
        self.recorder_queue = recorder_queue
        self._gl_ready = threading.Event()

    def wait_gl_ready(self, timeout: float = 5.0) -> bool:
        return self._gl_ready.wait(timeout)

    def run(self) -> None:
        if not glfw.init():
            print("[renderer] glfw.init failed")
            return

        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.RESIZABLE, glfw.TRUE)
        glfw.window_hint(glfw.FLOATING, glfw.FALSE)
        glfw.window_hint(glfw.FOCUSED, glfw.TRUE)
        glfw.window_hint(glfw.VISIBLE, glfw.TRUE)

        params = self.state.snapshot_params()
        window = glfw.create_window(params.width, params.height,
                                    "AnalogVFX preview", None, None)
        if not window:
            glfw.terminate()
            print("[renderer] failed to create window")
            return
        # position the preview to the right of where dpg might be
        glfw.set_window_pos(window, 600, 80)
        glfw.show_window(window)
        glfw.focus_window(window)
        glfw.make_context_current(window)
        glfw.swap_interval(0)
        print("[renderer] preview window created. "
              "Click 'Старт' in the control panel to begin.")

        ctx = moderngl.create_context()
        self._gl_ready.set()

        vert = _load("passthrough.vert")
        progs = {
            "vhs":         ctx.program(vertex_shader=vert, fragment_shader=_load("vhs.frag")),
            "rgb_split":   ctx.program(vertex_shader=vert, fragment_shader=_load("rgb_split.frag")),
            "glitch":      ctx.program(vertex_shader=vert, fragment_shader=_load("glitch.frag")),
            "crt":         ctx.program(vertex_shader=vert, fragment_shader=_load("crt.frag")),
            "drip":        ctx.program(vertex_shader=vert, fragment_shader=_load("drip.frag")),
            "dither":      ctx.program(vertex_shader=vert, fragment_shader=_load("dither.frag")),
            "datamosh":    ctx.program(vertex_shader=vert, fragment_shader=_load("datamosh.frag")),
            "pixel_swap":  ctx.program(vertex_shader=vert, fragment_shader=_load("pixel_swap.frag")),
            "mirror_flow": ctx.program(vertex_shader=vert, fragment_shader=_load("mirror_flow.frag")),
            "light_accum": ctx.program(vertex_shader=vert, fragment_shader=_load("light_bleed_accum.frag")),
            "light_comp":  ctx.program(vertex_shader=vert, fragment_shader=_load("light_bleed_comp.frag")),
            # cyber-vision effects
            "luma_key":     ctx.program(vertex_shader=vert, fragment_shader=_load("luma_key.frag")),
            "duotone":      ctx.program(vertex_shader=vert, fragment_shader=_load("duotone.frag")),
            "heavy_grain":  ctx.program(vertex_shader=vert, fragment_shader=_load("heavy_grain.frag")),
            "motion_trails":ctx.program(vertex_shader=vert, fragment_shader=_load("motion_trails.frag")),
            "quad_split":   ctx.program(vertex_shader=vert, fragment_shader=_load("quad_split.frag")),
            "cctv_grid":    ctx.program(vertex_shader=vert, fragment_shader=_load("cctv_grid.frag")),
            "hud_overlay":  ctx.program(vertex_shader=vert, fragment_shader=_load("hud_overlay.frag")),
            # maximalist
            "bloom_h":      ctx.program(vertex_shader=vert, fragment_shader=_load("bloom_h.frag")),
            "bloom_v":      ctx.program(vertex_shader=vert, fragment_shader=_load("bloom_v.frag")),
            "dual_tone":    ctx.program(vertex_shader=vert, fragment_shader=_load("dual_tone.frag")),
            "fragment_layers": ctx.program(vertex_shader=vert, fragment_shader=_load("fragment_layers.frag")),
            "pixel_sort":   ctx.program(vertex_shader=vert, fragment_shader=_load("pixel_sort.frag")),
            "wave_warp":    ctx.program(vertex_shader=vert, fragment_shader=_load("wave_warp.frag")),
            "depth_apply":  ctx.program(vertex_shader=vert, fragment_shader=_load("depth_apply.frag")),
            "depth_view":   ctx.program(vertex_shader=vert, fragment_shader=_load("depth_view.frag")),
            "ascii_mosaic": ctx.program(vertex_shader=vert, fragment_shader=_load("ascii_mosaic.frag")),
            "final":        ctx.program(vertex_shader=vert, fragment_shader=_load("final.frag")),
        }
        vbo = ctx.buffer(QUAD_VERTS.tobytes())
        vaos = {
            name: ctx.vertex_array(p, [(vbo, "2f 2f", "in_pos", "in_uv")])
            for name, p in progs.items()
        }
        # Cache uniform names per program so the hot path can do a set-lookup
        # instead of try/except KeyError. moderngl's Program acts like a dict
        # of members; we filter to uniforms by attribute kind.
        prog_uniforms = {}
        for name, prog in progs.items():
            try:
                members = set(prog)   # iterating Program yields member names
            except Exception:
                members = set()
            prog_uniforms[name] = members

        def safe_set(prog_name, key, value):
            """Set a uniform only if it actually survived compilation."""
            if key in prog_uniforms.get(prog_name, ()):
                progs[prog_name][key] = value

        input_tex = None
        ping = None
        pong = None
        prev_textures: dict[str, tuple] = {}
        bloom_accum = None
        bloom_prev = None
        flow_tex = None
        flow_size = (0, 0)
        last_flow_version = -1
        cur_w = cur_h = 0
        last_size = (0, 0)
        overlay_tex = None
        overlay_canvas = None  # BGRA numpy buffer
        frame_idx = 0
        last_det_version = -1
        cached_det = None

        bloom_h_buf = None     # 1st-pass horizontal bloom buffer
        cctv_mem_short = None
        cctv_mem_mid   = None
        cctv_mem_long  = None
        cctv_counter = 0

        depth_tex = None       # state.depth_map uploaded as R32F
        depth_size = (0, 0)
        last_depth_version = -1
        depth_snapshot = None  # FBO holding the frame at the last depth_save

        # ---- ASCII glyph atlas (live-regenerated when params.ascii_text changes) ----
        ascii_tex = None
        ascii_glyph_count = 8
        current_ascii_text = ""

        def _rebuild_ascii_atlas(text: str):
            nonlocal ascii_tex, ascii_glyph_count, current_ascii_text
            arr, count, _cw = make_atlas_array(text)
            h, w = arr.shape
            if ascii_tex is not None:
                ascii_tex.release()
            ascii_tex = ctx.texture((w, h), 1, arr.tobytes())
            ascii_tex.repeat_x = ascii_tex.repeat_y = False
            ascii_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
            ascii_glyph_count = int(count) if count > 0 else 1
            current_ascii_text = text
            print(f"[renderer] ASCII atlas regenerated: {count} glyphs ('{text!s}')")

        try:
            _rebuild_ascii_atlas(ASCII_DEFAULT)
        except Exception as e:
            print(f"[renderer] ASCII atlas init failed: {e}")

        spout = None
        if HAS_SPOUT:
            spout = SpoutGL.SpoutSender()
            spout.setSenderName(params.sender_name)
        last_sender = params.sender_name

        start = time.monotonic()

        def reset_resources(w, h):
            nonlocal input_tex, ping, pong, prev_textures
            nonlocal bloom_accum, bloom_prev, overlay_tex, overlay_canvas
            nonlocal bloom_h_buf
            nonlocal cctv_mem_short, cctv_mem_mid, cctv_mem_long
            nonlocal depth_snapshot
            for r in [input_tex, overlay_tex]:
                if r is not None:
                    r.release()
            for fbo_pair in [ping, pong, bloom_accum, bloom_prev, bloom_h_buf,
                             cctv_mem_short, cctv_mem_mid, cctv_mem_long,
                             depth_snapshot,
                             *prev_textures.values()]:
                if fbo_pair is not None:
                    fbo_pair[0].release()
                    fbo_pair[1].release()
            input_tex = ctx.texture((w, h), 3)
            input_tex.repeat_x = input_tex.repeat_y = False
            input_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            ping = _make_fbo(ctx, w, h)
            pong = _make_fbo(ctx, w, h)
            prev_textures = {k: _make_fbo(ctx, w, h) for k in _EFFECTS_WITH_PREV}
            bloom_accum = _make_fbo(ctx, w, h)
            bloom_prev  = _make_fbo(ctx, w, h)
            bloom_h_buf = _make_fbo(ctx, w, h)
            cctv_mem_short = _make_fbo(ctx, w, h)
            cctv_mem_mid   = _make_fbo(ctx, w, h)
            cctv_mem_long  = _make_fbo(ctx, w, h)
            depth_snapshot = _make_fbo(ctx, w, h)
            overlay_tex = ctx.texture((w, h), 4)
            overlay_tex.repeat_x = overlay_tex.repeat_y = False
            overlay_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            overlay_canvas = np.zeros((h, w, 4), dtype=np.uint8)

        while not self.state.stop_event.is_set() and not glfw.window_should_close(window):
            glfw.poll_events()
            params = self.state.snapshot_params()

            if not params.running:
                ctx.screen.use()
                ctx.clear(0.05, 0.05, 0.05, 1.0)
                glfw.swap_buffers(window)
                time.sleep(0.03)
                continue

            frame = self.state.get_frame()
            if frame is None:
                time.sleep(0.005)
                continue

            h, w = frame.shape[:2]
            if (w, h) != last_size:
                reset_resources(w, h)
                cur_w, cur_h = w, h
                last_size = (w, h)
                glfw.set_window_size(window, w, h)

            assert input_tex is not None and ping is not None and pong is not None

            if spout is not None and params.sender_name != last_sender:
                spout.setSenderName(params.sender_name)
                last_sender = params.sender_name

            # ---- upload current camera frame ----
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb = cv2.flip(rgb, 0)
            input_tex.write(np.ascontiguousarray(rgb))

            # ---- upload depth map if new ----
            depth_arr, depth_ver = self.state.get_depth_map()
            if depth_arr is not None and depth_ver != last_depth_version:
                dh, dw = depth_arr.shape[:2]
                if depth_tex is None or depth_size != (dw, dh):
                    if depth_tex is not None:
                        depth_tex.release()
                    depth_tex = ctx.texture((dw, dh), 1, dtype="f4")
                    depth_tex.repeat_x = depth_tex.repeat_y = False
                    depth_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                    depth_size = (dw, dh)
                # depth from DepthThread is top-row-first like the camera frame;
                # GL textures expect bottom-row-first, so flip on upload.
                depth_flipped = np.ascontiguousarray(depth_arr[::-1])
                depth_tex.write(depth_flipped.tobytes())
                last_depth_version = depth_ver

            # ---- upload optical flow if new ----
            flow_arr, flow_ver = self.state.get_flow()
            if flow_arr is not None and flow_ver != last_flow_version:
                fh, fw = flow_arr.shape[:2]
                if flow_tex is None or flow_size != (fw, fh):
                    if flow_tex is not None:
                        flow_tex.release()
                    flow_tex = ctx.texture((fw, fh), 2, dtype="f4")
                    flow_tex.repeat_x = flow_tex.repeat_y = False
                    flow_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                    flow_size = (fw, fh)
                flow_tex.write(flow_arr.tobytes())
                last_flow_version = flow_ver

            t = time.monotonic() - start
            resolution = (float(cur_w), float(cur_h))

            # ---- pull latest detection result (if changed) ----
            det_obj, det_ver = self.state.get_detection()
            if det_ver != last_det_version:
                cached_det = det_obj
                last_det_version = det_ver

            frame_idx += 1
            if frame_idx == 1:
                print(f"[renderer] first frame rendered ({cur_w}x{cur_h}). "
                      f"Preview window: 'AnalogVFX preview' — may be hidden behind the GUI.")

            # ---- pipeline: blit input → ping, then iterate effects ----
            ping[1].use()
            ctx.clear()
            input_tex.use(0)
            progs["final"]["u_tex"] = 0
            progs["final"]["u_gain"] = 1.0
            progs["final"]["u_contrast"] = 1.0
            progs["final"]["u_saturation"] = 1.0
            progs["final"]["u_flip_y"] = 0
            vaos["final"].render(moderngl.TRIANGLE_STRIP)

            src, dst = ping, pong

            for cfg in params.effects:
                if not cfg.enabled or cfg.mix <= 0.0001:
                    continue
                kind = cfg.kind

                if kind == "bloom_glow":
                    # ---- pass 1: horizontal bloom seed ----
                    bloom_h_buf[1].use()
                    ctx.clear()
                    src[0].use(0)
                    p = progs["bloom_h"]
                    p["u_tex"] = 0
                    p["u_resolution"] = resolution
                    p["u_threshold"] = params.bloom_threshold
                    p["u_radius"]    = params.bloom_radius
                    vaos["bloom_h"].render(moderngl.TRIANGLE_STRIP)

                    # ---- pass 2: vertical + composite onto src → dst ----
                    dst[1].use()
                    ctx.clear()
                    src[0].use(0)
                    bloom_h_buf[0].use(1)
                    p = progs["bloom_v"]
                    p["u_tex"] = 0
                    p["u_bloom_h"] = 1
                    p["u_resolution"] = resolution
                    p["u_radius"]    = params.bloom_radius
                    p["u_intensity"] = params.bloom_intensity
                    p["u_tint"] = (params.bloom_tint_r, params.bloom_tint_g, params.bloom_tint_b)
                    p["u_mix"] = cfg.mix
                    vaos["bloom_v"].render(moderngl.TRIANGLE_STRIP)
                    src, dst = dst, src
                    continue

                if kind == "ascii_mosaic":
                    if ascii_tex is None:
                        continue
                    # live-update atlas if user typed something else
                    wanted_text = (params.ascii_text or ASCII_DEFAULT)
                    if wanted_text != current_ascii_text:
                        try:
                            _rebuild_ascii_atlas(wanted_text)
                        except Exception as e:
                            print(f"[renderer] ASCII atlas rebuild failed: {e}")
                    dst[1].use()
                    ctx.clear()
                    src[0].use(0)
                    ascii_tex.use(1)
                    p = progs["ascii_mosaic"]
                    p["u_tex"] = 0
                    p["u_atlas"] = 1
                    p["u_resolution"]    = resolution
                    p["u_cell_size"]     = params.ascii_cell_size
                    p["u_glyph_count"]   = float(ascii_glyph_count)
                    p["u_invert"]        = params.ascii_invert
                    p["u_color_source"]  = params.ascii_color_source
                    p["u_tint"]          = (params.ascii_tint_r, params.ascii_tint_g, params.ascii_tint_b)
                    p["u_mix"]           = cfg.mix
                    vaos["ascii_mosaic"].render(moderngl.TRIANGLE_STRIP)
                    src, dst = dst, src
                    continue

                if kind == "depth_save":
                    # snapshot current src FBO so depth_apply later can revert
                    if depth_snapshot is not None:
                        ctx.copy_framebuffer(depth_snapshot[1], src[1])
                    continue

                if kind == "depth_apply":
                    if depth_tex is None or depth_snapshot is None:
                        continue
                    dst[1].use()
                    ctx.clear()
                    src[0].use(0)
                    depth_snapshot[0].use(1)
                    depth_tex.use(2)
                    p = progs["depth_apply"]
                    p["u_tex"] = 0
                    p["u_prev"] = 1
                    p["u_depth"] = 2
                    p["u_depth_min"] = params.depth_min
                    p["u_depth_max"] = params.depth_max
                    p["u_feather"]   = params.depth_feather
                    p["u_invert"]    = params.depth_invert
                    p["u_mix"]       = cfg.mix
                    vaos["depth_apply"].render(moderngl.TRIANGLE_STRIP)
                    src, dst = dst, src
                    continue

                if kind == "depth_view":
                    if depth_tex is None:
                        continue
                    dst[1].use()
                    ctx.clear()
                    src[0].use(0)
                    depth_tex.use(1)
                    p = progs["depth_view"]
                    p["u_tex"] = 0
                    p["u_depth"] = 1
                    p["u_mode"]   = params.depth_view_mode
                    p["u_gain"]   = params.depth_view_gain
                    p["u_offset"] = params.depth_view_offset
                    p["u_invert"] = params.depth_view_invert
                    p["u_mix"]    = cfg.mix
                    vaos["depth_view"].render(moderngl.TRIANGLE_STRIP)
                    src, dst = dst, src
                    continue

                if kind == "cctv_grid":
                    # update memory buffers on their own clocks
                    cctv_counter += 1
                    if cctv_counter % 6 == 0:
                        ctx.copy_framebuffer(cctv_mem_short[1], src[1])
                    if cctv_counter % 30 == 0:
                        ctx.copy_framebuffer(cctv_mem_mid[1], src[1])
                    if cctv_counter % 90 == 0:
                        ctx.copy_framebuffer(cctv_mem_long[1], src[1])

                    dst[1].use()
                    ctx.clear()
                    src[0].use(0)
                    cctv_mem_short[0].use(1)
                    cctv_mem_mid[0].use(2)
                    cctv_mem_long[0].use(3)
                    p = progs["cctv_grid"]
                    p["u_tex"] = 0
                    p["u_mem_short"] = 1
                    p["u_mem_mid"] = 2
                    p["u_mem_long"] = 3
                    p["u_time"]   = t
                    p["u_cols"]   = params.cctv_cols
                    p["u_rows"]   = params.cctv_rows
                    p["u_gap"]    = params.cctv_gap
                    p["u_invert_mask"] = float(int(params.cctv_invert_mask))
                    p["u_delay_mask"]  = float(int(params.cctv_delay_mask))
                    p["u_tint_strength"]   = params.cctv_tint_strength
                    p["u_desat"]           = params.cctv_desat
                    p["u_label_brightness"]= params.cctv_label_brightness
                    p["u_mix"] = cfg.mix
                    vaos["cctv_grid"].render(moderngl.TRIANGLE_STRIP)
                    src, dst = dst, src
                    continue

                if kind not in progs:
                    continue

                dst[1].use()
                ctx.clear()
                src[0].use(0)
                prog = progs[kind]

                if kind in ("drip", "mirror_flow", "motion_trails"):
                    prog["u_current"] = 0
                    prev_tex, _ = prev_textures[kind]
                    prev_tex.use(1)
                    prog["u_prev"] = 1
                elif kind == "hud_overlay":
                    # rebuild CPU overlay each frame, upload, bind on unit 1
                    accent_bgr = (
                        int(round(params.accent_b * 255)),
                        int(round(params.accent_g * 255)),
                        int(round(params.accent_r * 255)),
                    )
                    ovcfg = OverlayConfig(
                        show_face_box=params.hud_show_face_box,
                        show_face_keypoints=params.hud_show_face_keypoints,
                        show_face_mesh=params.hud_show_face_mesh,
                        show_pose=params.hud_show_pose,
                        show_vectors=params.hud_show_vectors,
                        show_text=params.hud_show_text,
                        show_crosshairs=params.hud_show_crosshairs,
                        show_eyes=params.hud_show_eyes,
                        show_pupils=params.hud_show_pupils,
                        show_hands=params.hud_show_hands,
                        show_blobs=params.hud_show_blobs,
                        accent_bgr=accent_bgr,
                        line_thickness=int(params.hud_line_thickness),
                        text_top_left=params.hud_text_top_left,
                        text_top_right=params.hud_text_top_right,
                        text_bottom_left=params.hud_text_bottom_left,
                        text_bottom_right=params.hud_text_bottom_right,
                        text_face_label=params.hud_text_face_label,
                        text_blob_label=params.hud_text_blob_label,
                    )
                    if cached_det is not None and overlay_canvas is not None:
                        cached_det.width = cur_w
                        cached_det.height = cur_h
                        render_overlay(overlay_canvas, cached_det, ovcfg, frame_idx)
                    elif overlay_canvas is not None:
                        overlay_canvas.fill(0)
                    # cv2 BGRA → RGBA swap so the GL shader sees correct channels
                    bgra = overlay_canvas
                    rgba = bgra[..., [2, 1, 0, 3]]
                    # flip vertically (GL origin convention, like input frame)
                    rgba = np.ascontiguousarray(rgba[::-1])
                    overlay_tex.write(rgba.tobytes())
                    prog["u_tex"] = 0
                    overlay_tex.use(1)
                    prog["u_overlay"] = 1
                elif kind == "datamosh":
                    prog["u_tex"] = 0
                    prev_tex, _ = prev_textures[kind]
                    prev_tex.use(1)
                    prog["u_prev"] = 1
                    if flow_tex is not None:
                        flow_tex.use(2)
                        prog["u_flow"] = 2
                    else:
                        src[0].use(2)
                        prog["u_flow"] = 2
                else:
                    prog["u_tex"] = 0
                    if kind in _EFFECTS_WITH_PREV:
                        prev_tex, _ = prev_textures[kind]
                        prev_tex.use(1)
                        prog["u_prev"] = 1

                self._set_uniforms(prog, kind, params, t, resolution, cfg.mix,
                                   prog_uniforms.get(kind, set()))

                vaos[kind].render(moderngl.TRIANGLE_STRIP)

                if kind in _EFFECTS_WITH_PREV:
                    ctx.copy_framebuffer(prev_textures[kind][1], dst[1])

                src, dst = dst, src

            # ---- final pass to screen ----
            fb_w, fb_h = glfw.get_framebuffer_size(window)
            ctx.screen.use()
            ctx.viewport = (0, 0, fb_w, fb_h)
            ctx.clear()
            src[0].use(0)
            progs["final"]["u_tex"] = 0
            progs["final"]["u_gain"] = params.gain
            progs["final"]["u_contrast"] = params.contrast
            progs["final"]["u_saturation"] = params.saturation
            progs["final"]["u_flip_y"] = 0
            vaos["final"].render(moderngl.TRIANGLE_STRIP)

            # ---- Spout ----
            if spout is not None:
                spout.sendTexture(src[0].glo, 0x0DE1, cur_w, cur_h, True, 0)

            # ---- recorder ----
            if (params.recording and self.recorder_queue is not None
                    and not self.recorder_queue.full()):
                buf = src[1].read(components=3, dtype="f1")
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(cur_h, cur_w, 3)
                arr = np.ascontiguousarray(arr[::-1])
                try:
                    self.recorder_queue.put_nowait((cur_w, cur_h, arr))
                except queue.Full:
                    pass

            glfw.swap_buffers(window)

        # cleanup
        if input_tex is not None:
            input_tex.release()
        if flow_tex is not None:
            flow_tex.release()
        if overlay_tex is not None:
            overlay_tex.release()
        if depth_tex is not None:
            depth_tex.release()
        if ascii_tex is not None:
            ascii_tex.release()
        for fbo_pair in [ping, pong, bloom_accum, bloom_prev, bloom_h_buf,
                         cctv_mem_short, cctv_mem_mid, cctv_mem_long,
                         depth_snapshot,
                         *prev_textures.values()]:
            if fbo_pair is not None:
                fbo_pair[0].release()
                fbo_pair[1].release()
        for v in vaos.values():
            v.release()
        vbo.release()
        for p in progs.values():
            p.release()
        if spout is not None:
            spout.releaseSender()
        ctx.release()
        glfw.destroy_window(window)
        glfw.terminate()
        self.state.stop_event.set()

    @staticmethod
    def _set_uniforms(prog, kind, params, t, resolution, mix_amt, members):
        def U(name, value):
            if name in members:
                prog[name] = value
        U("u_mix", mix_amt)
        U("u_time", t)
        U("u_resolution", resolution)
        if kind == "vhs":
            U("u_tracking_noise", params.vhs_tracking_noise)
            U("u_line_jitter",    params.vhs_line_jitter)
            U("u_chroma_blur",    params.vhs_chroma_blur)
            U("u_color_loss",     params.vhs_color_loss)
            U("u_head_switch",    params.vhs_head_switch)
        elif kind == "rgb_split":
            U("u_shift",      params.rgb_shift_amount)
            U("u_wave_freq",  params.rgb_wave_frequency)
            U("u_wave_speed", params.rgb_wave_speed)
        elif kind == "glitch":
            U("u_block_size",    params.glitch_block_size)
            U("u_drop_chance",   params.glitch_drop_chance)
            U("u_repeat_chance", params.glitch_repeat_chance)
        elif kind == "crt":
            U("u_scanline",    params.crt_scanline_strength)
            U("u_mask",        params.crt_mask_strength)
            U("u_curvature",   params.crt_curvature)
            U("u_vignette",    params.crt_vignette)
            U("u_persistence", params.crt_persistence)
        elif kind == "drip":
            U("u_drip",  params.drip_speed)
            U("u_decay", params.drip_decay)
        elif kind == "dither":
            U("u_levels",          params.dither_levels)
            U("u_grid_size",       params.dither_grid_size)
            U("u_osc_a_freq",      params.dither_osc_a_freq)
            U("u_osc_b_freq",      params.dither_osc_b_freq)
            U("u_osc_a_speed",     params.dither_osc_a_speed)
            U("u_osc_b_speed",     params.dither_osc_b_speed)
            U("u_mix_mode",        params.dither_mix_mode)
            U("u_trace_thickness", params.dither_trace_thickness)
            U("u_jitter",          params.dither_jitter)
            U("u_chromatic",       params.dither_chromatic)
            U("u_polarity",        params.dither_polarity)
            U("u_color_mode",      params.dither_color_mode)
            U("u_algorithm",       getattr(params, "dither_algorithm", 0.0))
            U("u_color_a", (params.dither_color_a_r,
                            params.dither_color_a_g,
                            params.dither_color_a_b))
            U("u_color_b", (params.dither_color_b_r,
                            params.dither_color_b_g,
                            params.dither_color_b_b))
        elif kind == "datamosh":
            U("u_strength",     params.datamosh_strength)
            U("u_freeze",       params.datamosh_freeze)
            U("u_period",       params.datamosh_period)
            U("u_breakthrough", params.datamosh_breakthrough)
            U("u_jitter",       params.datamosh_jitter)
            U("u_chaos",        params.datamosh_chaos)
        elif kind == "pixel_swap":
            U("u_threshold",   params.pixel_swap_threshold)
            U("u_radius",      params.pixel_swap_radius)
            U("u_chaos",       params.pixel_swap_chaos)
            U("u_directional", params.pixel_swap_directional)
            U("u_temporal",    params.pixel_swap_temporal)
        elif kind == "mirror_flow":
            U("u_drip",   params.mirror_drip)
            U("u_decay",  params.mirror_decay)
            U("u_unroll", params.mirror_unroll)
            U("u_axis_h", params.mirror_axis_h)
            U("u_axis_v", params.mirror_axis_v)
            U("u_swirl",  params.mirror_swirl)
            U("u_chaos",  params.mirror_chaos)
        elif kind == "luma_key":
            U("u_lift",   params.luma_lift)
            U("u_gain",   params.luma_gain)
            U("u_gamma",  params.luma_gamma)
            U("u_invert", params.luma_invert)
        elif kind == "duotone":
            U("u_accent",   (params.accent_r, params.accent_g, params.accent_b))
            U("u_shadow",   (0.0, 0.0, 0.0))
            U("u_midpoint", params.duotone_midpoint)
            U("u_strength", params.duotone_strength)
        elif kind == "heavy_grain":
            U("u_amount", params.grain_amount)
            U("u_size",   params.grain_size)
            U("u_chroma", params.grain_chroma)
            U("u_blotch", params.grain_blotch)
        elif kind == "motion_trails":
            U("u_decay",     params.trails_decay)
            U("u_threshold", params.trails_threshold)
            U("u_blur",      params.trails_blur)
            U("u_drift",     (params.trails_drift_x, params.trails_drift_y))
            U("u_tint",      (params.trails_tint_r, params.trails_tint_g, params.trails_tint_b))
            U("u_secondary", params.trails_secondary)
            U("u_mode",      params.trails_mode)
            U("u_polarity",  params.trails_polarity)
            U("u_chaos",     params.trails_chaos)
        elif kind == "quad_split":
            U("u_gap",         params.quad_gap)
            U("u_invert_mask", float(int(params.quad_invert_mask)))
            U("u_freeze_q",    params.quad_freeze)
        elif kind == "hud_overlay":
            U("u_brightness", params.hud_brightness)
        elif kind == "dual_tone":
            U("u_shadow",    (params.dual_shadow_r, params.dual_shadow_g, params.dual_shadow_b))
            U("u_mid",       (params.dual_mid_r, params.dual_mid_g, params.dual_mid_b))
            U("u_highlight", (params.dual_high_r, params.dual_high_g, params.dual_high_b))
            U("u_split",     params.dual_split)
            U("u_strength",  params.dual_strength)
        elif kind == "fragment_layers":
            U("u_count",  params.frag_count)
            U("u_height", params.frag_height)
            U("u_offset", params.frag_offset)
            U("u_alpha",  params.frag_alpha)
        elif kind == "pixel_sort":
            U("u_threshold",    params.psort_threshold)
            U("u_length",       params.psort_length)
            U("u_direction",    params.psort_direction)
            U("u_band_density", params.psort_band_density)
            U("u_band_jitter",  params.psort_band_jitter)
            U("u_band_speed",   params.psort_band_speed)
            U("u_length_var",   params.psort_length_var)
            U("u_edge_fade",    params.psort_edge_fade)
            U("u_color_mode",   params.psort_color_mode)
            U("u_axis",         params.psort_axis)
            U("u_algorithm",    params.psort_algorithm)
            U("u_chromatic",    params.psort_chromatic)
            U("u_blend",        params.psort_blend)
        elif kind == "wave_warp":
            U("u_amp",   (params.wave_amp1,   params.wave_amp2,   params.wave_amp3))
            U("u_freq",  (params.wave_freq1,  params.wave_freq2,  params.wave_freq3))
            U("u_phase", (params.wave_phase1, params.wave_phase2, params.wave_phase3))
            U("u_speed", (params.wave_speed1, params.wave_speed2, params.wave_speed3))
            U("u_shape", (params.wave_shape1, params.wave_shape2, params.wave_shape3))
            U("u_angle",       params.wave_angle)
            U("u_polar",       params.wave_polar)
            U("u_chromatic",   params.wave_chromatic)
            U("u_band_height", params.wave_band_height)
            U("u_band_y",      params.wave_band_y)
            U("u_band_soft",   params.wave_band_soft)
