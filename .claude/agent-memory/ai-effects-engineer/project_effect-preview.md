---
name: project_effect-preview
description: effect_preview.py — генератор превью-миниатюр для шейдерных эффектов; архитектурные решения и граничные случаи.
metadata:
  type: project
---

`effect_preview.py` создан в корне проекта. Генерирует превью (120x80 по умолчанию) через moderngl standalone context.

**Why:** нужен для UI (DPG) — показывать миниатюру каждого эффекта без запуска видео.

**How to apply:** при любой работе с UI эффектов — ссылаться на этот файл как источник превью.

## Ключевые решения

- Каждый вызов `generate_preview(kind)` создаёт отдельный `moderngl.create_standalone_context()` и после рендера его `release()`-ит. Это безопасно при вызове из любого потока, но медленнее, чем один постоянный контекст.
- Кэш `_preview_cache` хранит ключ `"{kind}_{w}x{h}"` — размер учитывается.
- 47 шейдеров, все проходят. 3 класса проблемных:
  - `dither.frag` требует `u_blue_noise` — подаём синтетический 64×64 тайл.
  - `motion_trails.frag` требует `u_current` вместо `u_tex` — у него особый main-tex-uniform.
  - `datamosh.frag`, `motion_trails.frag` требуют `u_prev`, `u_flow` — подаём копию тестового кадра.
- Flip Y обязателен: OpenGL читает FBO снизу вверх, `result[::-1].copy()`.
- Все uniform-ы устанавливаются через try/except (`_set_uniform`) — шейдер без конкретного uniform-а не падает.
