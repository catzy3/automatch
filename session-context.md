# session-context.md
*Snapshot for continuing work on **shi o kiku** / AutoMatch.*
Last updated: 2026-06-15 (сессия: CRT/logo/датамош в v1 + **AUTOMATCH 2 — Rust-порт**)

---

## TL;DR (сессии 14–15.06)

Две части.

**A. Доделки в v1 (`analogvfx/`, Python):**
- Заменил легаси-эффект `crt` на `crt_monitor` везде (pipeline/GUI/smart_random, offline+live); `crt.frag` оставлен на диске; легаси `crt_*` поля в state.py сохранены для совместимости. Попутно починил `_refresh_browser` NameError и краш `motion_blur` (float в `uniform int u_samples` → `int(...)`).
- ASCII-лого «shi o kiku» вспышкой на первом бите (превью — GL-проход поверх, экспорт — CPU поверх кадра, чтобы эффекты не съели); клик по лого в GUI → поле ввода → `[shi o kiku]` → меню-тумблер вспышки.
- **Битстрим-датамош (Avidemux-style)** — `datamosh_bitstream.py`: bloom (удаление I-кадров) + smear (дубль P-кадров) + fine-scale; пост-обработка на экспорте + GUI-секция (`DatamoshRenderSettings`).
- v1 запушен в приватный **github.com/catzy3/automatch** (аккаунт catzy3, git credential manager, без gh CLI).

**B. AUTOMATCH 2 — новый Rust-порт (основная масса сессии):**
- Отдельное дерево **`automatch2/`** (НЕ трогаем v1). Стек: **Rust + eframe(winit+glutin+glow) + egui**, GLSL-шейдеры из v1 переносятся дословно, ffmpeg CLI-пайп, serde.
- **Детальная continuity — в `automatch2/PLAN.md`, раздел §0 ТЕКУЩИЙ СТАТУС** (читать первым по v2). Там же дорожная карта C0–C9, журнал, чеклист.
- Готово: C0–C5 (окно→GFX-ядро→медиа-декод→граф эффектов→аудио/таймлайн→экспорт+нативный датамош) + бóльшая часть Phase 6 + Phase 7.
- **26 эффектов** с глубокими параметрами (incl. порт v1 `datamosh.frag` + 8 наавторённых через Workflow и скомпилированных на GPU). Beat-sync→эффекты (`u_beat`), ModMatrix (LFO), feedback + history-ring инфра.
- **Монтажный движок**: папка клипов → ClipPool → таймлайн → переключение клипов с **crossfade**; работает в живом превью и на экспорте.
- **Прямой GL-рендер** в egui-вьюпорт (PaintCallback+Blitter, без CPU-readback). Битстрим-датамош (Avidemux) на экспорте + UI. Пресеты (8). Сохранение/загрузка сессий (serde JSON). Ретро-глитч тема, инспектор справа, диагностика свёрнута.
- **Бинарь:** `automatch2/target/release/automatch2.exe` (cargo build [--release]). Env-хуки для headless-проверок: `AM2_SELFTEST` (ffmpeg-selftest'ы), `AM2_AUTOEXPORT=<path>`, `AM2_MONTAGE_DIR=<dir>`, `AM2_DATAMOSH`.
- **Авто-цикл**: scheduled task `automatch2-progress-checkin`, cron `*/30 * * * *` (каждые 30 мин) — читает PLAN.md §0, делает шаг, коммитит/пушит.
- **Открытое/дальше**: верхний тулбар (завершить лейаут); экспорт чанками в `update()` + прогресс-бар (GL на главном потоке → не отдельный thread); random in-points монтажа; оптимизация (Phase 8); **проверить ориентацию превью вживую** (flip=true — не подтверждено глазами).

---

## TL;DR (сессии 30–31.05)

Масштабная сессия: code review, 13 новых шейдеров, ModMatrix GUI, Effect Chain (drag → two-click swap), KILL ALL / bypass_montage, детальные side-панели для всех 27 эффектов. Браузер эффектов попытались сделать — удалили из-за неразрешимого GL context конфликта (задокументировано в ISSUES.md).

---

## USER_EFFECTS (27 эффектов, все полностью реализованы)

```python
USER_EFFECTS = (
    "luma_key", "duotone", "motion_trails", "wave_warp", "pixel_sort",
    "datamosh", "vhs", "ascii_mosaic", "crt", "dither", "rgb_split",
    "edges", "pixel_swap", "kaleidoscope", "slit_scan", "distort",
    "thermal", "cellular", "elastic_grid", "crt_monitor",
    "found_footage", "chromatic_aberration", "color_posterize", "swirl",
    "iridescent_foil", "motion_blur", "advanced_glitch",
)
```

---

## Архитектура (стабильная)

| Модуль | Состояние |
|---|---|
| `shi_o_kiku.py` | entry point |
| `automatch.py` | ~4300 строк: MediaPool, OffscreenRenderer, build_timeline, AudioAnalysis, blob-трекеры, ModMatrix, _apply_effect_chain, bypass_montage |
| `automatch_gui.py` | ~4600 строк: GL1TCH тема, матрица 27 эффектов + side-panel (все 27 с детальными параметрами), ModMatrix GUI (LFO+routes), Effect Chain two-click swap, KILL ALL, bypass_montage |
| `automatch_mod.py` | ModMatrix core: 4 LFO, 9 источников, BEAT_SPIKE_PRESET, DOWNBEAT_PULSE_PRESET |
| `state.py` / `renderer.py` / `gui.py` | live tool |
| `shaders/` | 47 .frag файлов |
| `ISSUES.md` | задокументированная проблема GL context для будущего effect browser |

---

## Что сделано (30–31.05)

### Багфиксы (коммиты 0d5f85c → 3222a92)
- `state.trails.clear()` при смене tracker mode
- `[TRACK]`/`[SCAN]` семантика вместо `[LOCK]`/`[SCAN]`
- Bayer 8×8 b2-swap исправлен
- Blue-noise texture binding в live-tool renderer.py
- `_BLEND_FX` очищен от удалённых эффектов
- Animated Bayer dither (algo 1/2, demoscene flickering)

### Новые шейдеры — 7 эффектов (942b04b → a080c02)
- `found_footage.frag` — dropout bands, ghost echo, chroma bleed
- `chromatic_aberration.frag` — radial RGB split + animated spin
- `color_posterize.frag` — 3 mode + Bayer dither
- `swirl.frag` — aspect-correct vortex
- `iridescent_foil.frag` — luma-gradient hue shift + metallic specular
- `motion_blur.frag` — directional + zoom-blur
- `advanced_glitch.frag` — block sync glitch + color split
- `heavy_grain.frag` — апгрейд: algo=1 Perlin fBm
- `crt_monitor.frag` — апгрейд: u_aberration_mode (0/1/2)

### ModMatrix GUI Slice 2 (ec4a6b6)
- 4 LFO + 8 routes + BEAT SPIKE / DOWNBEAT PULSE кнопки

### Side-панели для всех 27 эффектов (a080c02)
- vhs, crt, wave_warp, datamosh, motion_trails, rgb_split, duotone, luma_key, edges, pixel_swap + все 7 новых

### KILL ALL + bypass_montage (90e479c + 6607095)
- `KILL ALL` — выключает все эффекты включая locked
- `БЕЗ МОНТАЖА` — отключает beat-sync нарезку, рендерит исходник целиком

### Effect Chain two-click swap (03ecfdd + e657a27)
- Чекбокс "Цепочка" в строке матрицы
- Первый клик = выделить (зелёная рамка, без rebuild DOM)
- Второй клик на другую ячейку = swap + rebuild
- Кнопка ↺ сброс порядка
- `_CHAIN_ORDER: list` + `AutoEditSettings.effect_chain`
- `_apply_effect_chain(snap, chain)` в preview() и run()

### Браузер эффектов — удалён (fd648e2)
- Попытка сделать floating window с preview шейдеров провалилась
- Конфликт: `moderngl.create_standalone_context()` vs DPG GLFW/WGL context
- Проблема задокументирована в `ISSUES.md`

---

## Что **не сделано** (открытые задачи)

| # | Задача | Откуда |
|---|---|---|
| 1 | **Пересобрать exe** — stale с 26.05, НЕТ ничего из сессий 28-31.05 | exe stale |
| 2 | **Прокликать GUI вживую** — после всех изменений ни разу не делали полный прогон | — |
| 3 | **~10 пресетов** под матрицу (Cyber Noir / GitS / Dreamcore) | user 27.05 |
| 4 | **Effect Browser с превью** — нужно pre-bake до `dpg.create_context()` | ISSUES.md |
| 5 | **Старые preset JSON** в корне — dead weight | — |

---

## Известные проблемы

1. **Effect Browser preview** — `moderngl.create_standalone_context()` конфликтует с DPG GL context. Future solution: pre-bake все превью до `dpg.create_context()`, сохранять как `.npy`, загружать при старте.
2. **Exe stale** — build.bat застрял на `pause`, последняя сборка 26.05.
3. **`models/blue_noise_64.npy`** — генерируется при первом render/preview.
4. **DPG segfault headless** — нельзя вызывать render без вьюпорта.
5. **Effect Chain** — тайлы не обновляют цвет при смене стейта в матрице автоматически (нужно вручную или через `_rebuild_matrix()`).

---

## Состояние артефактов

| Что | Состояние |
|---|---|
| Source code | Актуальный, 21 коммит поверх initial |
| Frozen exe | **STALE — 26.05.2026 02:41, 7.7MB** |
| Git | `main`, 21 коммит, нет remote |
| `ISSUES.md` | Создан 31.05 — effect browser GL conflict |

---

## Git log (последние коммиты)

```
fd648e2 revert: remove effect browser (preview GL conflict unresolved)
e657a27 fix: chain two-click + browser preview via background thread
a178151 fix: use add_dynamic_texture for browser preview
3263312 feat: effect browser with live preview
03ecfdd fix: replace broken DPG drag-drop with two-click swap for chain reorder
570e48a fix: drag-drop — set payload_type+drop_callback in button constructor
6607095 fix: matrix visual reorder + bypass_montage replaces bypass_effects
66fe5a0 fix: drag-drop chain on existing matrix cells
c409a15 feat: effect chain as drag-drop tiles inside effects section
dea28ee feat: effect chain — user-defined render order
90e479c feat: KILL ALL button + bypass effects toggle
a080c02 feat: motion_blur + advanced_glitch + detailed side panels for all 27 effects
98606ac feat: heavy_grain Perlin algo(1)
dd1be19 feat: iridescent_foil effect
3f2b3c9 feat: animated Bayer dither
942b04b feat: 4 new effects (found_footage, chromatic_aberration, color_posterize, swirl)
ec4a6b6 feat: ModMatrix GUI Slice 2
3222a92 fix: remove stale effects from _BLEND_FX
ce6b5ff fix: bind blue-noise texture in live-tool renderer
9b1a058 fix: correct Bayer 8x8 b2 lookup
0d5f85c fix: blob tracker state bugs + import cleanup
b9dd0a5 Initial commit
```

---

## Критические файлы

- `automatch.py:435` — `USER_EFFECTS` (27)
- `automatch.py:286` — `AutoEditSettings` (effect_chain, bypass_montage)
- `automatch.py:_apply_effect_chain` — реордер per cut
- `automatch_gui.py:_build_effects_section` — матрица + side-panel + chain
- `automatch_gui.py:_CHAIN_ORDER` — глобальный порядок цепочки
- `automatch_gui.py:_chain_cell_click` — two-step swap логика
- `automatch_gui.py:_rebuild_matrix` — перестройка матрицы
- `automatch_gui.py:_build_mod_matrix_section` — ModMatrix GUI
- `automatch_mod.py:BEAT_SPIKE_PRESET / DOWNBEAT_PULSE_PRESET`
- `ISSUES.md` — задокументированные нерешённые проблемы

---

## Quick start следующей сессии

```powershell
cd C:\Users\divin\Code\analogvfx

# Проверка импортов
$env:PYTHONIOENCODING="utf-8"
C:\Python312\python.exe -c "import automatch, automatch_gui; print('OK')"

# Запуск GUI
.\launch.bat

# Пересборка exe (нужна!)
.\build.bat
```
