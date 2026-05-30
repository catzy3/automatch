# session-context.md
*Snapshot for continuing work on **shi o kiku** / AutoMatch.*
Last updated: 2026-05-30 (сессия code review + 13 новых эффектов + ModMatrix GUI + Effect Chain + детальные панели)

---

## TL;DR (сессия 30.05)

Гигантская сессия — полный code review, аудит всех эффектов, 13 новых шейдеров, ModMatrix GUI (Slice 2), Effect Chain drag-drop, KILL ALL / bypass toggle, детальные side-панели для всех 27 эффектов.

---

## USER_EFFECTS (27 эффектов, все полностью реализованы)

```python
USER_EFFECTS = (
    "luma_key", "duotone", "motion_trails", "wave_warp", "pixel_sort",
    "datamosh", "vhs", "ascii_mosaic", "crt", "dither", "rgb_split",
    "edges", "pixel_swap", "kaleidoscope", "slit_scan", "distort",
    "thermal", "cellular", "elastic_grid", "crt_monitor",
    # Добавлены 30.05:
    "found_footage", "chromatic_aberration", "color_posterize", "swirl",
    "iridescent_foil", "motion_blur", "advanced_glitch",
)
```

---

## Архитектура (стабильная)

| Модуль | Состояние |
|---|---|
| `shi_o_kiku.py` | entry point с crash logger + path management |
| `automatch.py` | ~4200 строк, core auto-editor: MediaPool, OffscreenRenderer, build_timeline, AudioAnalysis, blob-трекеры, ModMatrix, _apply_effect_chain |
| `automatch_gui.py` | DearPyGui, GL1TCH тема, матрица 27 эффектов + side-panel (все 27 с детальными параметрами), ModMatrix GUI, Effect Chain drag-drop, ~4500 строк |
| `automatch_mod.py` | ModMatrix core: 4 LFO, 9 источников, маршруты, BEAT_SPIKE_PRESET, DOWNBEAT_PULSE_PRESET |
| `state.py` / `renderer.py` / `gui.py` | live tool (отдельный режим), 23 эффекта |
| `audio.py` / `modulation.py` / `detection.py` / `overlay.py` | живой тул |
| Build | `shi_o_kiku.spec` + `build.bat` + `launch.bat`, ~271 MB onedir |
| `shaders/` | 47 .frag файлов |

---

## Что сделано в сессии 30.05

### Багфиксы (коммиты 0d5f85c → 3222a92)
- `state.trails.clear()` при смене tracker mode в blob-трекере
- `[TRACK]`/`[SCAN]` вместо `[LOCK]`/`[SCAN]` — семантически верно
- `USER_EFFECTS_NO_MIX` вынесен в top-level import
- Bayer 8×8 b2-swap исправлен в `dither.frag` (каноническая матрица)
- Blue-noise texture binding в live-tool `renderer.py` (algo=3 выдавал мусор)
- `_BLEND_FX` — удалены "modulate" и "pixelate" (убраны из USER_EFFECTS)

### Animated Bayer (коммит 3f2b3c9)
- `dither.frag` algo 1/2: time-varying spatial offset → flickering demoscene dither

### Новые шейдеры — 7 эффектов (коммиты 942b04b → a080c02)
- `found_footage.frag` — dropout bands, ghost echo, chroma bleed, luma crush, scan warp
- `chromatic_aberration.frag` — radial RGB split, animated spin
- `color_posterize.frag` — 3 mode (luma/per-channel/palette-snap) + Bayer dither
- `swirl.frag` — aspect-correct vortex
- `iridescent_foil.frag` — luma-gradient hue shift + metallic specular
- `motion_blur.frag` — directional + zoom-blur, N=4..16 samples
- `advanced_glitch.frag` — block sync glitch + color split
- `heavy_grain.frag` — апгрейд: algo=1 → 3-octave Perlin fBm + вертикальная ориентация
- `crt_monitor.frag` — апгрейд: u_aberration_mode (0=radial, 1=barrel+split, 2=scanline-phase)

### ModMatrix GUI — Slice 2 (коммит ec4a6b6)
- 4 LFO: wave combo (sine/tri/saw/sq/rand/s&h), rate, beat_sync, phase, reset_on_downbeat
- 8 routes: target (effect mixes + 30 state params), source (off/energy/rms/beat/downbeat/drop/lfo1-4), mode, depth, curve, lo/hi, invert, combine
- Кнопки пресетов: **BEAT SPIKE** (beat→effect mixes, lo=0.3, hi=0.85) и **DOWNBEAT PULSE** (downbeat)
- BEAT_SPIKE_PRESET, DOWNBEAT_PULSE_PRESET в `automatch_mod.py`

### Детальные side-панели для всех 27 эффектов (коммит a080c02)
- До: 13 эффектов имели side-панели
- После: **все 27** имеют детальные слайдеры в side-panel
- Добавлены: vhs, crt, wave_warp, datamosh, motion_trails, rgb_split, duotone, luma_key, edges, pixel_swap + все 7 новых

### KILL ALL + Bypass (коммит 90e479c)
- `KILL ALL` — выключает все эффекты включая locked (в строке матрицы рядом с CLEAR)
- `БЕЗ ЭФФЕКТОВ (bypass)` — чекбокс над Preview/Render, не меняет матрицу

### Effect Chain drag-drop (коммиты dea28ee → c409a15)
- Drag-drop тайлы (6×5 grid, 62×46px) встроены в блок "Эффекты" под матрицей
- Перетаскивай тайл на другой для смены порядка рендера
- Те же цвета что матрица (grey/green/magenta/cyan)
- `AutoEditSettings.effect_chain: list` — кастомный порядок
- `_apply_effect_chain(snap, chain)` в preview() и run()
- Чекбокс "Активировать цепочку" + СБРОС

---

## Что **не сделано** (открытые задачи)

| # | Задача | Оценка | Откуда |
|---|---|---|---|
| 1 | **Пересобрать exe** (`build.bat`) — stale с 26.05, в нём НЕТ ничего из сессий 28-30.05 | ~5-10 мин build | exe stale |
| 2 | **Прокликать GUI вживую** — после объёма правок 30.05 никто не кликал | визуальная проверка | — |
| 3 | **~10 пресетов** под матрицу (Cyber Noir / GitS / Dreamcore) | ~1 ч | user 27.05, перенесено |
| 4 | **Старые preset JSON** в корне — dead weight, loader фильтрует | ~5 мин | было давно |

---

## Известные проблемы / quirks

1. **`models/blue_noise_64.npy`** — генерируется при первом `OffscreenRenderer.__init__` (первый render/preview). В dev-режиме файл появится после первого рендера. В live-туле — уже создаётся при старте renderer.py.
2. **Stabilization синхронна** на первом обращении к каждому видео — async через daemon thread, но первые кадры идут без стабилизации (by design, default off).
3. **Старые preset JSON** в корне (`laincore.json`, `cctv.json` и др.) — loader фильтрует (`state.py:413`), dead weight.
4. **CUDA warnings** при PyInstaller analysis — bitsandbytes excluded, на работу не влияет.
5. **DPG segfault headless** — нельзя звать render без вьюпорта.
6. **Effect Chain guard** `_CHAIN_TILES_BUILT` — защита от двойного билда тайлов.

---

## Состояние артефактов

| Что | Где | Состояние |
|---|---|---|
| Source code | `C:\Users\divin\Code\analogvfx\` | актуальный, 13 коммитов поверх initial |
| Frozen exe | `dist\shi_o_kiku\shi_o_kiku.exe` | **STALE — 29.05.2026 02:41, 7.7MB.** НЕТ ничего из сессий 28-30.05. Пересобрать. |
| Blue-noise cache | `models\blue_noise_64.npy` | генерируется при первом render |
| Pexels API key | `pexels_config.json` | user'ская |
| Last session | `automatch_last_session.json` | автозагружается |
| Git | `main`, 13 коммитов, нет remote | local only |

---

## Git log (все коммиты)

```
c409a15 feat: effect chain as drag-drop tiles inside effects section
dea28ee feat: effect chain — user-defined render order
90e479c feat: KILL ALL button + bypass effects toggle
a080c02 feat: motion_blur + advanced_glitch + detailed side panels for all 27 effects
98606ac feat: heavy_grain Perlin algo (algo=1) — organic film grain
dd1be19 feat: iridescent_foil effect — holographic foil overlay
3f2b3c9 feat: animated Bayer dither (algo 1/2) — demoscene flickering
942b04b feat: 4 new effects — found_footage, chromatic_aberration, color_posterize, swirl
ec4a6b6 feat: ModMatrix GUI (Slice 2) — LFO + route panel wired to automatch_mod
3222a92 fix: remove stale effects from _BLEND_FX
ce6b5ff fix: bind blue-noise texture in live-tool renderer for dither algo=3
9b1a058 fix: correct Bayer 8x8 b2 lookup in dither.frag
0d5f85c fix: blob tracker state bugs + import cleanup
b9dd0a5 Initial commit: shi o kiku / AutoMatch VFX tool
```

---

## Критические файлы

- `automatch.py:435` — `USER_EFFECTS` (27 эффектов)
- `automatch.py:286` — `AutoEditSettings.effect_chain`
- `automatch.py:_apply_effect_chain` — реордер snap.effects по chain
- `automatch.py:_set_effect_params` — все 27 эффектов
- `automatch_gui.py:_build_effects_section` — матрица + side-panel + chain tiles
- `automatch_gui.py:_CHAIN_ORDER` — глобальный порядок цепочки
- `automatch_gui.py:_refresh_chain_tiles` — обновление тайлов после drag-drop
- `automatch_gui.py:_build_mod_matrix_section` — ModMatrix GUI
- `automatch_gui.py:_collect_settings` — всё собирается здесь
- `automatch_mod.py:BEAT_SPIKE_PRESET / DOWNBEAT_PULSE_PRESET`
- `shaders/` — 47 .frag файлов

---

## Gotchas

- **`build.bat`** killает процесс exe и чистит dirs вручную, потом PyInstaller.
- **Effect Chain tiles** строятся один раз (`_CHAIN_TILES_BUILT` guard) — не вызывать дважды.
- **DPG drag_payload** требует `payload_type="CHAIN_TILE"` на обоих концах.
- **`_refresh_chain_tiles()`** нужно вызывать после любого изменения матрицы (смены стейта эффекта) чтобы цвета тайлов совпадали. Сейчас это **НЕ** автоматически — потенциальное улучшение.
- OpenCV legacy trackers: `cv2.legacy.TrackerCSRT_create` etc. (opencv-contrib-python 4.13.0).
- DPG tags уникальны на app — префиксовать именем фичи.

---

## Возможные следующие шаги

1. **Пересобрать exe** (`.\build.bat`) — критично, всё stale
2. **Прокликать GUI** (`.\launch.bat`) — 27 эффектов, drag-drop chain, ModMatrix, новые side-панели
3. **Пресеты** (~10 под матрицу: Cyber Noir / GitS / Dreamcore) — самый запрошенный незакрытый
4. **`_refresh_chain_tiles()` после смены стейта** — тайлы не обновляют цвет автоматически при клике в матрице
5. **Удалить старые preset JSON** из корня — dead weight
