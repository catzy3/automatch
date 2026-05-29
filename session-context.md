# session-context.md
*Snapshot for continuing work on **shi o kiku** / AutoMatch.*
Last updated: 2026-05-28 (сессия GL1TCH-редизайна + 4 альтернативных алгоритма)

---

## TL;DR (сессия 28.05)

Эта сессия = большой UI-редизайн + четыре новых "альтернативных алгоритма", каждый с выбором для пользователя.

1. **Матрица эффектов** (вместо вертикальной таблицы) — сетка 4×3 ячеек в стиле Infiltrator/GL1TCH. Каждая ячейка имеет **4 состояния**: `off → on → RND → locked` (клик циклит). Справа — side-panel с параметрами выбранного эффекта.
   - `locked` (LCK) = заморозить mix + игнорировать "Все RND". Вниз по пайплайну эмитится как `on`.
   - Аббревиатуры текстом (без иконок), цвета: off=серый, on=зелёный, RND=magenta, locked=cyan; выбранная ячейка — яркая зелёная рамка.
2. **GL1TCH-стиль на всё окно** — по референсу "GL1TCH//CORE v0.66". Плоская тема (rounding=0, FrameBorderSize=1), палитра magenta/cyan/neon-green на near-black, шрифт Consolas. Header-strip "SHI O KIKU // AUTOMATCH v0.66" с corruption-полосами, footer "STATUS :: ready".
3. **Дизерные текстуры** — процедурные glitch-полосы сверху/снизу (`_make_glitch_strip`) + dither-strip разделители секций (`_make_dither_strip`), как static-текстуры DPG.
4. **Четыре альтернативных алгоритма** (по образцу того, как у pixel_sort уже был выбор):
   - **ASCII** — кастомный текст (можно вписать свои слова), пресеты, cell size, invert, color-source, tint.
   - **Pixel sort** — добавлен Kim Asendorf (algo 4 горизонт / 5 вертикаль) к существующим DV25-режимам; combo выбора + threshold + length + color mode.
   - **Dither** — добавлены Bayer 4×4 / Bayer 8×8 / Blue noise к оригинальному circuit-modulation; combo `dither_algorithm`.
   - **Blob tracking** — добавлены OpenCV-трекеры CSRT / KCF / MOSSE к HSV-режиму; combo `tracker_mode`. Zero новых зависимостей (opencv-contrib уже стоит).

Всё прошло headless smoke-test (3 шейдера компилируются под GL 3.3, 3 OpenCV-трекера доступны, GUI roundtrip collect→dump→restore для всех 4 фич, `_track_blobs` end-to-end). **GUI визуально пользователем ещё НЕ прокликан**, exe **НЕ пересобран** — это следующие шаги.

---

## Архитектура (стабильная)

| Модуль | Состояние |
|---|---|
| `shi_o_kiku.py` | entry point с crash logger + path management (frozen / dev) |
| `automatch.py` | ~3200 строк, core auto-editor: `MediaPool`, `OffscreenRenderer`, `build_timeline`, `AudioAnalysis`, blob-трекеры |
| `automatch_gui.py` | DearPyGui, **GL1TCH-тема**, матрица эффектов + side-panel, ~2700 строк |
| `state.py` / `renderer.py` / `gui.py` | live tool (отдельный режим), 23 эффекта, dither с алгоритмами |
| `audio.py` / `modulation.py` / `detection.py` / `overlay.py` | живой тул: audio thread, mod engine, face/hand/blob detection, HUD |
| Build | `shi_o_kiku.spec` + `build.bat` + `launch.bat`, ~271 MB onedir |
| `shaders/` | `pixel_sort.frag` (algo 0-5), `dither.frag` (algo 0-3), `ascii_mosaic.frag` |

---

## Что сделано в этой сессии — детали по файлам

### `automatch_gui.py`
- **Палитра/состояния** (~279): `_STATE_CYCLE=["off","on","random","locked"]`, `_STATE_LABELS`, `_FX_ABBR`, GL1TCH-консты `_GL_BG/_GREEN/_MAGENTA/_CYAN/_RED/_TEXT/_DIM`. `_FX_STATE` default `"off"`. `_SELECTED_FX`, `_BTN_THEMES_SEL`.
- `_build_btn_themes()` — 4 темы состояний + selected-варианты (яркая зелёная рамка).
- `_set_fx_state` / `_cycle_fx_state` (off→on→RND→locked→off, вызывает `_select_fx`) / `_select_fx` / `_randomize_all_effects` (пропускает locked) / `_reset_all_effects` (off всё, пропускает locked) / `_collect_effect_overrides` (locked→on downstream).
- `_refresh_side_panel` / `_on_side_mix_change` / `_on_side_rnd_change`.
- `_apply_theme()` переписан под плоский GL1TCH. `_load_font(size=14)` Consolas (`consola.ttf`). `_section_sep()` — magenta `//` + dither-strip.
- `_build_effects_section()` — матрица 4×3 (ячейки 84×46) + side-panel child_window: имя, mix-слайдер, rand-mix чекбокс, под-панели pixel sort / ASCII / dither по выбору.
- Strip-хелперы (~2475): `_build_strip_themes`, `_build_header_strip`, `_build_footer_strip` (host тега `status`).
- Текстуры: `_make_glitch_strip(w,h,seed)`, `_make_dither_strip(w,h)`, `_register_glitch_textures()` → `_TEX_GLITCH_TOP/BOT/DITHER_SEP`.
- **ASCII**: `_apply_ascii_preset`, `_collect_ascii_text`, `_collect_ascii_params`, `_apply_ascii_dict`. Под-панель `fx_side_ascii` (`am_ascii_text`, presets, `am_ascii_cell_size`, `am_ascii_invert_chk`, `am_ascii_color_source_chk`, `am_ascii_tint`).
- **Pixel sort**: `_PSORT_ALGO_LABELS` (6), `_PSORT_COLOR_MODES`, `_collect_psort_params`, `_apply_psort_params`. Под-панель `fx_side_psort` (`am_psort_algo_combo`, `am_psort_threshold`, `am_psort_length`, `am_psort_color_mode_combo`).
- **Dither**: `_DITHER_ALGOS` (4), `am_dither_algo_combo`; `_collect/_apply_dither_params` расширены `dither_algorithm`.
- **Blob**: `_BLOB_TRACKER_MODES` / `_BLOB_TRACKER_KEYS=("hsv","csrt","kcf","mosse")`, `bt_tracker_mode_combo`, `_collect_blob_track` (label→key), `_apply_blob_track(d)`.
- `_collect_settings` + `_dump_session` + `_apply_session` зеркалят `ascii_text/ascii_params/psort_params/blob_track`.
- `main()` (~2303): header-strip + glitch-top, footer + glitch-bottom; вызывает `_build_strip_themes()` + `_register_glitch_textures()`.

### `automatch.py`
- **ХОТФИКС 28.05 (после запуска):** `analyze_audio_full` (~582) — librosa 11 / numpy 2.3.5 ломали BPM. `librosa.beat.beat_track` теперь возвращает tempo как `(1,)` ndarray, а `float(bpm)` падал с `TypeError: only 0-dimensional arrays...` → каждый запуск уходил в `_onset_fallback`, где BPM клампится к потолку **200.0** (отсюда "постоянно 200"). Фикс: `bpm = float(np.asarray(bpm).reshape(-1)[0])` сразу после beat_track. Проверено: синтетический 120-BPM клик → 120.0. **NB:** env = numpy 2.3.5 + librosa 11.0.0 — если всплывут другие librosa-вызовы с похожим array/scalar mismatch, чинить так же.
- `BlobTrackSettings` (~102): `tracker_mode: str = "hsv"`.
- `AutoEditSettings` (~200): `ascii_text=""`, `ascii_params: dict`, `psort_params: dict`.
- Blob-трекинг (~1095): `_OPENCV_TRACKER_MODES`, `_make_opencv_tracker(mode)`, `_BlobTrackerState`, `_track_blobs(frame_bgr, bt, state)` — HSV passthrough или persistent OpenCV multi-tracker с HSV re-seeding.
- `OffscreenRenderer.__init__`: `current_ascii_text=ASCII_DEFAULT`, `blue_noise_tex=self._bake_blue_noise_tex()`.
- `_bake_blue_noise_tex(size=64)` — numpy iterative HPF blue-noise (seed `0xB10E_5EED`), кэш `models/blue_noise_64.npy`.
- `_rebuild_ascii_atlas(text)`.
- `render()`: ASCII-ветка ребейкает атлас если `params.ascii_text` отличается; dither-ветка биндит `blue_noise_tex` в slot 1 + `u_blue_noise`.
- `_set_effect_params` dither: `U("u_algorithm", getattr(params,"dither_algorithm",0.0))`.
- `preview()/run()` per-cut: применяют `ascii_params/psort_params`, ставят `snap.ascii_text`. Frame-loops: `blob_tracker_state=_BlobTrackerState(mode=...)` до цикла, оба callsite используют `_track_blobs(...)`.

### `state.py`
- `dither_algorithm: float = 0.0` (0=circuit, 1=Bayer4, 2=Bayer8, 3=blue noise) после `dither_color_b_b` (~125).

### `renderer.py` (live tool)
- Dither-блок (~678): `U("u_algorithm", getattr(params,"dither_algorithm",0.0))`. **NB:** blue_noise НЕ биндится в live-туле (на практике algo 0); если экспонировать — добавить bind.

### `shaders/pixel_sort.frag`
- `int algo = int(clamp(u_algorithm,0.0,5.0))` в начале main(). Новая ветка `algo==4||5` (Kim Asendorf): per-row/col gate + segment scan (до 64px) + 8-sample bubble sort + rank lookup + опц. chromatic, early return. Algos 0-3 (DV25) без изменений.

### `shaders/dither.frag`
- `uniform sampler2D u_blue_noise; uniform float u_algorithm;`. Функции `bayer4(ivec2)` / `bayer8(ivec2)` (unrolled). main() ветвится `int algo = clamp(...,0,3)`: 0=circuit, 1=Bayer4, 2=Bayer8, 3=blue-noise tile. Остальной пайплайн (quantize/trace/color) общий.

---

## AutoMatch — что было сделано ранее (фундамент, стабильно)

- **Аудио**: `analyze_audio_full()` — HPSS percussion, downbeat phase-lock, onset/RMS/drop, manual BPM override.
- **Timeline**: 5 clip-modes (bag/shuffle/random/stride/reverse), per-cut energy/loudness/downbeat/phrase/drop флаги, `_choose_motion/_choose_transition`, beat-remap warp, random clip in-points (per-cut sub-RNG).
- **Размер кадра**: Horizontal/Vertical/Square/**Chaos**(дышит на 60fps)/Custom + chaos-mask на каждом pass.
- **Media**: папка ИЛИ один файл → N сегментов с random offsets; lazy duration (2ms vs 30s); camera stabilization.
- **Render**: Preview / Render / Slice(чисто) / Slice(эффекты) / **Batch N=2-50** / **Stop** (threading.Event) / **auto-numbered** (`automatch_1.mp4`...).
- **Эффекты physically-based**: vhs (NTSC composite), crt (deflection+convergence+P22), heavy_grain (H&D+log-normal), motion_trails (per-channel phosphor), wave_warp (curl-of-fBM), datamosh (defragmented), duotone/luma_key (Oklab), ascii_mosaic, pixel_sort, dither.

---

## Что **не сделано** (открытые задачи)

| # | Задача | Оценка | Откуда |
|---|---|---|---|
| 1 | **Прокликать GUI вживую** + скриншот (ASCII text+preset, PSORT combo, DTHR combo, Blob Tracker combo, проверить 4-стейт матрицу) | визуальная проверка | конец сессии 28.05 — НЕ сделано |
| 2 | **Пересобрать exe** (`build.bat`) — текущий от 26.05, в нём НЕТ ничего из этой сессии | ~5-10 мин build | exe stale |
| 3 | **~10 пресетов** под матрицу (Cyber Noir / GitS / Dreamcore) | ~1 ч | user 27.05, перенесено |
| 4 | **Font picker с превью** для text cards | ~30 мин | user 27.05 12:37, не начато |
| 5 | **Проверка остальных блок-эффектов** (dither duotone mode, cctv_grid, quad_split) | — | сделан pixel_sort+datamosh |
| 6 | **Async stabilisation** | — | первый кадр блокирует preview 2-5с при cam_stabilize=True (default off) |
| 7 | **Slit-scan + kaleidoscope shaders** | — | из старого плана |

---

## Известные проблемы / quirks

1. **`models/blue_noise_64.npy` ещё не сбейкан** — печётся при первом `OffscreenRenderer.__init__` (первый реальный render/preview). В этой сессии были только standalone shader-тесты, так что файла пока нет. Это нормально.
2. **Stabilization синхронна** на первом обращении к каждому видео — preview блокируется ~2-5с при `cam_stabilize=True`.
3. **Старые preset JSON** в корне (`laincore.json`, `cctv.json`, `circuit red.json`, `da.json`, `ergo.json`, `ass sort.json`, `preset123.json`) ссылаются на удалённые эффекты — loader фильтрует (`state.py:413`), но dead weight.
4. **CUDA warnings** при PyInstaller analysis (`cublas64_12.dll`) — bitsandbytes excluded, на работу не влияет.
5. **`automatch_last_session.json`** хранится рядом с exe(frozen)/скриптом(dev) — by design.
6. **DPG segfault headless** — нельзя звать `render_dearpygui_frame()`/`set_primary_window` без вьюпорта. Для smoke-теста строить секции напрямую в окне без event-loop.

---

## Состояние артефактов

| Что | Где | Состояние |
|---|---|---|
| Source code | `C:\Users\divin\Code\analogvfx\` | актуальный, все правки 28.05 in place (24 новых символа в gui, 20 в core) |
| Frozen exe | `dist\shi_o_kiku\shi_o_kiku.exe` | **STALE — 26.05.2026 02:00, 7.7MB.** Build 27.05 не завершился; работы 28.05 в нём НЕТ. Пересобрать. |
| Blue-noise cache | `models\blue_noise_64.npy` | **ещё нет** — сбейкается на первом render |
| Pexels API key | `pexels_config.json` | user'ская, нигде больше не хардкодить |
| Last session | `automatch_last_session.json` (28.05.2026 21:42) | автозагружается |

---

## История запросов сессии 28.05 (хронологически)

1. *"где проект analogvfx"* → подтвердили путь.
2. *"пропал диалог… восстанови самое последнее"* → восстановили контекст из транскрипта в этот файл.
3. *"сначала делаем матрицу. Сделай GUI по референсу?"* → матрица 4-стейт + GL1TCH по картинке.
4. *[референс-картинка GL1TCH//CORE v0.66]* → палитра/шрифт/strip'ы.
5. *"Доделай дизайн GUI"* → GL1TCH на всё окно.
6. *"ты обещал красивые текстуры дизерные"* → процедурные glitch/dither текстуры.
7. *"выбор размеров и символов в ascii (свои слова)… проверь GitHub по ascii/blobtrack/dithering, встрой готовые как альтернативные алгоритмы с выбором, как pixelsort"* → подтвердил все 4: ASCII custom, Kim Asendorf, Bayer/Blue-noise, CSRT/KCF/MOSSE. Все реализованы + smoke-test.
8. *"Сохрани эту сессию"* → этот апдейт.

---

## Критические файлы

- `automatch.py:~200` — `AutoEditSettings` (зеркалить новые поля в GUI collect/dump/apply)
- `automatch.py:~102` — `BlobTrackSettings` (`tracker_mode`)
- `automatch.py:~1095` — blob-трекеры (`_track_blobs`, `_BlobTrackerState`, `_make_opencv_tracker`)
- `automatch.py:build_timeline / preview() / run()` — таймлайн и циклы рендера
- `automatch_gui.py:~279` — состояния/палитра/темы
- `automatch_gui.py:_build_effects_section` — матрица + side-panel
- `automatch_gui.py:_collect_settings / _dump_session / _apply_session` — сериализация
- `state.py:~125` — `dither_algorithm`
- `shaders/pixel_sort.frag` / `shaders/dither.frag` — мульти-алгоритм ветки

---

## Gotchas

- **Никогда не запускать** `build\shi_o_kiku\shi_o_kiku.exe` (intermediate bootloader без DLL). Только `dist\…`.
- `build.bat` killает процесс exe и чистит dirs вручную, потом PyInstaller без `--clean`.
- Шейдеры ищутся через `Path(__file__).parent / "shaders"`; в frozen — внутри `_internal/`. User data — `Path(sys.executable).parent`.
- DPG tags уникальны на app — префиксовать именем фичи (`am_ascii_*`, `am_psort_*`, `am_dither_*`, `bt_*`).
- OpenCV legacy trackers: `cv2.legacy.TrackerCSRT_create/TrackerKCF_create/TrackerMOSSE_create` (opencv-contrib-python 4.13.0).

---

## Quick verification commands

```powershell
cd C:\Users\divin\Code\analogvfx
$env:PYTHONIOENCODING="utf-8"

# Все модули импортируются?
C:\Python312\python.exe -c "import importlib; [importlib.import_module(m) for m in ['state','renderer','gui','automatch','automatch_gui','shi_o_kiku']]; print('OK')"

# OpenCV-трекеры доступны?
C:\Python312\python.exe -c "import cv2; [print(n, hasattr(cv2.legacy, 'Tracker'+n+'_create')) for n in ['CSRT','KCF','MOSSE']]"

# Шейдеры компилируются (standalone GL)?
C:\Python312\python.exe -c "import moderngl; ctx=moderngl.create_standalone_context(); [print(p, 'OK') for p in ['pixel_sort','dither','ascii_mosaic']]"

# Свежесть build'а
Get-Item dist\shi_o_kiku\shi_o_kiku.exe | Select-Object LastWriteTime, Length

# Запуск GUI (dev)
.\launch.bat

# Пересборка exe
.\build.bat
```

---

## Возможные следующие шаги

1. **Прокликать GUI** (`.\launch.bat`) — 4-стейт матрица, ASCII (text+preset), PSORT combo, DTHR combo, Blob Tracker combo. Скриншот → визуальные правки.
2. **Пересобрать exe** (`.\build.bat`) — текущий устарел на 2 сессии.
3. **~10 пресетов** под матрицу (самый запрошенный из старого).
4. **Font picker с превью** — самый "лёгкий" незаконченный (~30 мин).
