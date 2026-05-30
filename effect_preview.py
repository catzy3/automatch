"""
effect_preview.py — генератор превью-миниатюр для каждого шейдерного эффекта.

Использование:
    from effect_preview import generate_preview, generate_all_previews

    rgba = generate_preview("vhs")          # np.ndarray (H, W, 4) float32, или None
    all_ = generate_all_previews(["vhs", "crt", "glitch"])

Зависимости: numpy, moderngl (уже установлены в проекте).
DearPyGui не используется.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

SHADER_DIR = Path(__file__).parent / "shaders"

# Размер превью по умолчанию
DEFAULT_W = 120
DEFAULT_H = 80

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Кэш превью (kind -> RGBA float32 массив)
# ---------------------------------------------------------------------------

_preview_cache: dict[str, np.ndarray] = {}


# ---------------------------------------------------------------------------
# Синтетический тестовый кадр
# ---------------------------------------------------------------------------

def _make_test_frame(w: int, h: int) -> np.ndarray:
    """Создаёт синтетический градиентный тестовый кадр.

    Returns
    -------
    np.ndarray
        Форма (H, W, 3), dtype float32, диапазон 0..1.
    """
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    r = xx
    g = yy
    b = (np.sin(xx * 8.0) * 0.5 + 0.5) * (np.cos(yy * 6.0) * 0.5 + 0.5)
    return np.stack([r, g, b], axis=2)  # (H, W, 3) float32


# ---------------------------------------------------------------------------
# Безопасная установка uniform-переменных
# ---------------------------------------------------------------------------

# Шейдеры, требующие нестандартного семплера для основного изображения
# (вместо стандартного "u_tex").  Ключ — имя эффекта, значение — имя uniform-а.
_MAIN_TEX_OVERRIDE: dict[str, str] = {
    "motion_trails": "u_current",
}

# Uniform-переменные, которые принимают текстуру: устанавливаем заглушку.
# Шейдеры, требующие u_prev (motion_trails, datamosh) или u_flow (datamosh),
# получат копию тестового кадра — эффект деградирует, но не упадёт.
_TEXTURE_UNIFORMS = {"u_prev", "u_blue_noise", "u_flow", "u_current"}

# Разумные дефолты для float-uniform-ов, специфичных для отдельных шейдеров.
# Без этих значений большинство uniform-ов просто останутся = 0.0 (дефолт OpenGL),
# что визуально "работает" для большинства эффектов.
_FLOAT_DEFAULTS: dict[str, float] = {
    # vhs
    "u_tracking_noise": 0.3,
    "u_line_jitter":    0.3,
    "u_chroma_blur":    0.5,
    "u_color_loss":     0.2,
    "u_head_switch":    0.4,
    # dither
    "u_levels":         8.0,
    "u_grid_size":      4.0,
    "u_osc_a_freq":    10.0,
    "u_osc_b_freq":     7.0,
    "u_osc_a_speed":    0.5,
    "u_osc_b_speed":    0.5,
    "u_mix_mode":       0.0,
    "u_trace_thickness": 0.25,
    "u_jitter":         0.3,
    "u_chromatic":      0.3,
    "u_polarity":       0.0,
    "u_color_mode":     1.0,
    "u_algorithm":      0.0,
    # motion_trails / datamosh
    "u_decay":         0.6,
    "u_threshold":     0.1,
    "u_blur":          0.5,
    "u_secondary":     0.3,
    "u_mode":          0.0,
    "u_chaos":         0.4,
    "u_strength":      0.5,
    "u_freeze":        0.3,
    "u_period":        2.0,
    "u_breakthrough":  0.3,
    # общие
    "u_mix":           1.0,
    "u_time":          0.5,
    "u_intensity":     1.0,
    "u_amount":        0.5,
    "u_speed":         1.0,
    "u_scale":         1.0,
    "u_brightness":    1.0,
    "u_contrast":      1.0,
    "u_saturation":    1.0,
    "u_hue":           0.0,
    "u_aberration":    0.5,
    "u_scanlines":     0.5,
    "u_noise":         0.3,
    "u_warp":          0.3,
    "u_angle":         0.0,
    "u_threshold":     0.5,
    "u_radius":        0.5,
    "u_frequency":     5.0,
    "u_amplitude":     0.3,
    "u_phase":         0.0,
    "u_zoom":          1.0,
}

_VEC2_DEFAULTS: dict[str, tuple[float, float]] = {
    "u_drift":      (0.0, 0.0),
    "u_offset":     (0.0, 0.0),
    "u_center":     (0.5, 0.5),
    "u_direction":  (1.0, 0.0),
}

_VEC3_DEFAULTS: dict[str, tuple[float, float, float]] = {
    "u_tint":    (1.0, 1.0, 1.0),
    "u_color":   (1.0, 1.0, 1.0),
    "u_color_a": (0.0, 0.0, 0.0),
    "u_color_b": (1.0, 1.0, 1.0),
}


def _set_uniform(prog, name: str, value) -> None:
    """Безопасно устанавливает uniform; молча игнорирует отсутствующие."""
    try:
        if name in prog:
            prog[name] = value
    except Exception:
        pass


def _bind_uniforms(
    ctx,
    prog,
    w: int,
    h: int,
    main_tex_unit: int,
    fallback_frame_rgb: np.ndarray,
) -> list:
    """Устанавливает все стандартные uniform-ы программы.

    Возвращает список созданных текстур-заглушек для последующего release().
    """
    extra_textures: list = []
    unit_counter = [main_tex_unit + 1]  # первые слоты заняты основной текстурой

    def next_unit() -> int:
        u = unit_counter[0]
        unit_counter[0] += 1
        return u

    # --- основной семплер ---
    _set_uniform(prog, "u_tex",     main_tex_unit)
    _set_uniform(prog, "u_current", main_tex_unit)  # motion_trails

    # --- разрешение и время ---
    _set_uniform(prog, "u_resolution", (float(w), float(h)))
    _set_uniform(prog, "u_time",       0.5)

    # --- текстурные заглушки (u_prev, u_blue_noise, u_flow) ---
    raw_fallback = (fallback_frame_rgb * 255).astype(np.uint8)
    for uname in ("u_prev", "u_flow"):
        if uname in prog:
            u = next_unit()
            try:
                tex = ctx.texture((w, h), 3, raw_fallback.tobytes())
                tex.use(u)
                _set_uniform(prog, uname, u)
                extra_textures.append(tex)
            except Exception:
                pass

    if "u_blue_noise" in prog:
        u = next_unit()
        try:
            # Генерируем синтетический blue-noise тайл 64×64
            rng = np.random.default_rng(42)
            bn = rng.random((64, 64, 1), dtype=np.float32)
            bn_u8 = (bn * 255).astype(np.uint8)
            tex = ctx.texture((64, 64), 1, bn_u8.tobytes())
            tex.repeat_x = True
            tex.repeat_y = True
            tex.use(u)
            _set_uniform(prog, "u_blue_noise", u)
            extra_textures.append(tex)
        except Exception:
            pass

    # --- float uniform-ы ---
    for name, val in _FLOAT_DEFAULTS.items():
        _set_uniform(prog, name, val)

    # --- vec2 uniform-ы ---
    for name, val in _VEC2_DEFAULTS.items():
        _set_uniform(prog, name, val)

    # --- vec3 uniform-ы ---
    for name, val in _VEC3_DEFAULTS.items():
        _set_uniform(prog, name, val)

    return extra_textures


# ---------------------------------------------------------------------------
# Рендер одного эффекта через moderngl (standalone context)
# ---------------------------------------------------------------------------

def _render_effect(kind: str, frame_rgb: np.ndarray, w: int, h: int) -> np.ndarray | None:
    """Рендерит эффект `kind` на тестовый кадр.

    Parameters
    ----------
    kind : str
        Имя шейдера (без расширения).
    frame_rgb : np.ndarray
        Входной кадр (H, W, 3) float32 0..1.
    w, h : int
        Размеры кадра.

    Returns
    -------
    np.ndarray | None
        (H, W, 4) float32 RGBA 0..1, или None при ошибке компиляции / отсутствия файла.
    """
    import moderngl  # отложенный импорт — не падает при отсутствии GPU на старте

    frag_path = SHADER_DIR / f"{kind}.frag"
    vert_path = SHADER_DIR / "passthrough.vert"

    if not frag_path.exists():
        log.debug("effect_preview: шейдер не найден: %s", frag_path)
        return None
    if not vert_path.exists():
        log.error("effect_preview: passthrough.vert не найден: %s", vert_path)
        return None

    try:
        vert_src = vert_path.read_text(encoding="utf-8")
        frag_src = frag_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("effect_preview: ошибка чтения шейдера %s: %s", kind, exc)
        return None

    ctx = None
    try:
        ctx = moderngl.create_standalone_context()

        # --- Компиляция программы ---
        try:
            prog = ctx.program(vertex_shader=vert_src, fragment_shader=frag_src)
        except Exception as exc:
            log.warning("effect_preview: ошибка компиляции '%s': %s", kind, exc)
            return None

        # --- Входная текстура (RGB) ---
        raw_in = (frame_rgb * 255).astype(np.uint8)
        tex_in = ctx.texture((w, h), 3, raw_in.tobytes())
        tex_in.use(0)

        # --- Установка всех uniform-ов ---
        extra_textures = _bind_uniforms(ctx, prog, w, h, 0, frame_rgb)

        # --- FBO для рендера ---
        tex_out = ctx.texture((w, h), 4)
        fbo = ctx.framebuffer(color_attachments=[tex_out])
        fbo.use()
        ctx.clear(0.0, 0.0, 0.0, 1.0)

        # --- Полноэкранный квад (TRIANGLE_STRIP) ---
        # Формат вершин: x, y (clip), u, v (tex)
        verts = np.array(
            [-1.0, -1.0, 0.0, 0.0,
              1.0, -1.0, 1.0, 0.0,
             -1.0,  1.0, 0.0, 1.0,
              1.0,  1.0, 1.0, 1.0],
            dtype="f4",
        )
        vbo = ctx.buffer(verts.tobytes())
        vao = ctx.vertex_array(prog, [(vbo, "2f 2f", "in_pos", "in_uv")])
        vao.render(moderngl.TRIANGLE_STRIP)

        # --- Чтение результата ---
        raw_out = fbo.read(components=4)
        result = (
            np.frombuffer(raw_out, dtype=np.uint8)
            .reshape(h, w, 4)
            .astype(np.float32)
            / 255.0
        )
        result = result[::-1].copy()  # flip Y (OpenGL bottom-left → top-left)

        # --- Освобождение ресурсов ---
        for tex in extra_textures:
            try:
                tex.release()
            except Exception:
                pass
        tex_in.release()
        tex_out.release()
        vbo.release()
        vao.release()
        fbo.release()
        prog.release()

        return result

    except Exception as exc:
        log.error("effect_preview: неожиданная ошибка при рендере '%s': %s", kind, exc)
        return None
    finally:
        if ctx is not None:
            try:
                ctx.release()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def generate_preview(
    kind: str,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
) -> np.ndarray | None:
    """Возвращает RGBA превью-миниатюру для эффекта `kind`.

    Результат кэшируется; повторные вызовы с тем же `kind` возвращают
    кэшированный массив без рендера.

    Parameters
    ----------
    kind : str
        Имя шейдера без расширения, например ``"vhs"``, ``"crt"``.
    width, height : int
        Размеры превью в пикселях. По умолчанию 120×80.

    Returns
    -------
    np.ndarray | None
        Форма (H, W, 4), dtype float32, диапазон 0..1 (RGBA).
        None если шейдер не найден или не компилируется.
    """
    cache_key = f"{kind}_{width}x{height}"
    if cache_key in _preview_cache:
        return _preview_cache[cache_key]

    frame = _make_test_frame(width, height)
    result = _render_effect(kind, frame, width, height)
    if result is not None:
        _preview_cache[cache_key] = result
    return result


def generate_all_previews(
    kinds: list[str],
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, np.ndarray | None]:
    """Генерирует превью для всех эффектов из списка.

    Parameters
    ----------
    kinds : list[str]
        Имена шейдеров (без расширения).
    width, height : int
        Размеры превью.
    callback : callable, optional
        Вызывается перед каждым рендером: ``callback(kind, index, total)``.
        Удобно для progress-bar в GUI.

    Returns
    -------
    dict[str, np.ndarray | None]
        Словарь kind -> массив RGBA (или None при ошибке).
    """
    results: dict[str, np.ndarray | None] = {}
    total = len(kinds)
    for i, kind in enumerate(kinds):
        if callback is not None:
            try:
                callback(kind, i, total)
            except Exception:
                pass
        results[kind] = generate_preview(kind, width, height)
    return results


def clear_cache() -> None:
    """Очищает кэш превью (например, при смене разрешения)."""
    _preview_cache.clear()


def list_available_effects() -> list[str]:
    """Возвращает список имён эффектов, для которых есть .frag файл."""
    return sorted(p.stem for p in SHADER_DIR.glob("*.frag"))
