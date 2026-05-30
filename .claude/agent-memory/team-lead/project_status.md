---
name: project-status
description: Текущий статус проекта shi o kiku / AutoMatch — что готово, что открыто, на 30.05.2026 (конец сессии)
metadata:
  type: project
---

Статус на 30.05.2026 (обновлено по git log).

**Готово (27 USER_EFFECTS + системные фичи):**
- vhs, crt, heavy_grain, motion_trails, wave_warp, pixel_sort, datamosh, duotone, luma_key, ascii_mosaic, dither, rgb_split, edges, pixel_swap, kaleidoscope, slit_scan, distort, thermal, cellular, elastic_grid, crt_monitor
- Новые (30.05): found_footage, chromatic_aberration, color_posterize, swirl, iridescent_foil
- Новые из backlog (30.05, считались низким приоритетом): **motion_blur**, **advanced_glitch** — оба закрыты в коммите a080c02
- Апгрейды (30.05): dither animated Bayer (algo 1/2), heavy_grain Perlin (algo=1)
- Blend-режимы (TouchDesigner-style Composite TOP) — GUI + shaders, _BLEND_FX dead code убран
- ModMatrix GUI (Slice 2) — 4 LFO + 8 routes + Beat-spike/Downbeat кнопки, beat_sync
- Font picker с превью (tc_font, _refresh_tc_preview)
- Матрица эффектов 4-state (off/on/RND/locked), GL1TCH тема
- AudioAnalysis: beats, downbeats, onset_env, rms, energy_at, rms_at, is_drop, is_downbeat
- User-defined effect chain (render order) — dea28ee
- KILL ALL button + bypass effects toggle — 90e479c
- Detailed side panels для всех 27 эффектов — a080c02

**Открытые задачи (приоритет убывает):**
1. Пересборка exe — stale с 26.05 (ВАЖНО, ~30 мин)
2. GUI не прокликан вживую пользователем после правок 30.05
3. Beat-spike decay — кнопки в GUI есть, снижение decay 0.18 → 0.04 сек для beat route — проверить automatch_mod.py, попало ли в код
4. ~10 пресетов (Cyber Noir / GitS / Dreamcore) — запрошено, не сделано
5. Font picker визуально не проверен вживую
6. Старые preset JSON в корне — dead weight

---

## Остаток backlog (низкий приоритет)

| # | Задача | Тип | Оценка |
|---|---|---|---|
| — | CRT+Chroma combo (upgrade crt_monitor, algo slot) | Upgrade | 1 ч |

**NO-GO (подтверждено):** liquid_metal, Bloom/God Rays/Lens Flare, motion_blur (закрыт), advanced_glitch (закрыт).

**Why:** Все P1–P7 + motion_blur + advanced_glitch из backlog закрыты. Оставшиеся задачи — операционные (exe build, визуальная проверка) или контент (пресеты).
**How to apply:** Следующая сессия — пересборка exe + ручная проверка GUI. Потом пресеты или CRT+Chroma upgrade.
