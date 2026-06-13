# AUTOMATCH 2 — мастер-план и журнал работ

> Единый файл-памятка. Раз в 5 часов я открываю его, читаю блок **ТЕКУЩИЙ СТАТУС**,
> сверяюсь с **ДОРОЖНОЙ КАРТОЙ**, делаю следующий маленький шаг, проверяю его
> по-настоящему (без костылей) и обновляю статус. Это инструкция самому себе.

---

## 0. ТЕКУЩИЙ СТАТУС  ⟵ обновлять на каждом чекпоинте

| Поле | Значение |
|---|---|
| Дата обновления | 2026-06-14 |
| Фаза | **Phase 0 — ЗАВЕРШЕНА** → начинаем Phase 1 |
| Шаг | C0 готов: cargo-скелет на eframe (winit+glow+egui), окно открывается |
| Последнее проверено (verified) | `cargo build` Finished (1m21s) + бинарник запущен, окно жило 8с без падения; egui-панель + glow-контекст рабочие |
| Следующее действие | Phase 1 — Core GFX: `gfx` (Shader/Program с кэшем юниформов, fullscreen-quad, FBO-пул, ping-pong) + passthrough-проход PNG на экран через `egui::PaintCallback`+`egui_glow` |
| Блокеры | нет |
| Последний чекпоинт-коммит | `e841b8e` (C0 scaffold) |
| Ветка/папка | `automatch2/` в репо `catzy3/automatch` |
| Окружение | Rust stable-msvc через rustup (cargo в `~/.cargo/bin`); MSVC-линкер из VS Community 2022 |

**Прогресс по фазам:** ▰▱▱▱▱▱▱▱▱▱ 1/10 (C0)

---

## 1. ПРОТОКОЛ САМОНАПОМИНАНИЯ (каждые 5 часов)

Когда срабатывает напоминание, делаю строго по шагам:

1. Открыть `automatch2/PLAN.md` → прочитать §0 **ТЕКУЩИЙ СТАТУС**.
2. Кратко отчитаться пользователю (на русском): фаза, шаг, что проверено, что дальше.
3. Сделать **один** следующий атомарный шаг из §8 (не больше — лучше маленький и проверенный).
4. **Проверить по-настоящему**: собралось → запустилось → даёт ожидаемый результат на реальных данных. Не «компилируется = готово».
5. Обновить §0 (дата, шаг, verified, следующее действие, блокеры).
6. Если закрыт чекпоинт фазы → коммит + push в `catzy3/automatch`, записать хэш в §0 и отметить чекбокс в §9.
7. Никогда не редактировать и не удалять файлы v1 (всё, что вне `automatch2/`).

---

## 2. ПРИНЦИПЫ (нерушимо)

- **Кусок → проверка → дальше.** Реализовал функцию — довёл до реально работающей — проверил на настоящих данных.
- **Корень, а не симптом.** Чиним первопричину, не лепим обход. Если тянет на костыль — стоп, ищем настоящую причину.
- **Без костылей.** Никаких «временных» хаков, закомментированных кусков «на потом», моков вместо реальной работы.
- **Чекпоинты в git.** Каждая завершённая под-цель = коммит + push. История = доказательство прогресса.
- **Референс-ориентированность.** Перед каждым крупным эффектом/подсистемой ищем 1–3 эталона на GitHub/ShaderToy и работаем под планку рынка.
- **Не трогаем v1.** `automatch` (Python) остаётся рабочим. `automatch2` — параллельное новое дерево.
- **Разноплановость эффектов.** Никаких новых «квадратиков», тупых перекрашиваний и постеризаций. Только сильные, виральные, визуально разные глитч/VFX-приёмы.
- **Оптимизация в основе, а не вдогонку.** Данные на GPU, минимум CPU↔GPU копий, конвейерная многопоточность с самого начала архитектуры.

---

## 3. ВИДЕНИЕ ПРОДУКТА

**Что строим:** нативный (Rust), быстрый, красивый инструмент для бит-синхронного авто-монтажа с
проф-уровнем глитч/VFX. Цель — **конкурентоспособный, уникальный продукт**: ниша между
«TikTok-эффектами в один тап» и тяжёлыми Resolume/TouchDesigner. Фишки-дифференциаторы:

- авто-монтаж под музыку (beat-sync) из коробки — этого нет у VJ-софта;
- проф-набор виральных глитч-эффектов с глубокими параметрами и модуляцией;
- нодовый граф эффектов + таймлайн с волной и битами — как у больших, но проще;
- реальный (битстрим) датамош и оптик-флоу мош в реальном времени;
- ретро-глитч эстетика интерфейса (то, что любит пользователь: breakcore/witch-house вайб).

**Платформа:** Windows-first (как сейчас), CMake-структура кросс-платформенная на будущее.

---

## 4. ЦЕЛЕВОЙ СТЕК (Rust)

Решение: **Rust + glow (OpenGL) + egui.** Главная причина — GLSL-шейдеры из v1 переносятся
дословно (glow — тонкая обёртка OpenGL, шейдеры остаются GLSL), плюс безопасность памяти,
`cargo` и один бинарник вместо боли CMake/PyInstaller.

| Слой | Выбор | Замена чему в v1 | Примечание |
|---|---|---|---|
| Язык | Rust (edition 2021) | Python | |
| Сборка | `cargo` (+ workspace) | — | зависимости одной строкой, без CMake/vcpkg |
| Окно/контекст | **`eframe`** (связывает winit+glutin+glow) | glfw (py), moderngl | даёт окно + raw glow-контекст |
| GPU | `glow` (OpenGL 4.x), через `eframe::glow` | moderngl | **GLSL-шейдеры переносим дословно**; кастомный GL в вьюпорт через `egui::PaintCallback` + `egui_glow::CallbackFn` |
| GUI | **`egui`** (immediate-mode, через `eframe`) | DearPyGui | стилизуем под ретро-глитч |
| Нодовый граф | `egui_node_graph` (или свой на egui) | — | граф эффектов |
| Графики/волна | `egui_plot` | — | таймлайн-волна, спектр |
| Аудио-анализ | `aubio` (биндинг) или свой онсет/темпо + `rustfft` | librosa | beat tracking без Python |
| Видео I/O | FFmpeg: сначала CLI-пайп (как v1), потом `ffmpeg-next` (libav) | imageio_ffmpeg/cv2 | фазируем: CLI → нативный libav |
| Картинка CPU | `image` crate | cv2 | минимум CPU-операций |
| Сериализация | `serde` + `serde_json` | json (py) | сессии/пресеты |
| Многопоточность | `std::thread` / `crossbeam` каналы | — | конвейер decode→render→encode |
| Депт/AI (опц.) | `ort` (onnxruntime) + depth_anything (в `models/`) | depth.py | стретч-цель |

Принцип: **GLSL-шейдеры из `analogvfx/shaders/` — наш актив.** Они переносимы; в automatch2 их
host-логика (uniforms, ping-pong, FBO) переезжает в Rust (glow), сами шейдеры копируем в
`automatch2/shaders/` и дорабатываем (НЕ редактируя оригиналы v1).

> Альтернатива на будущее: `wgpu` (Vulkan/DX12/Metal) — современнее, но требует WGSL,
> т.е. переписывания шейдеров. Отложено ради сохранения GLSL-актива.

---

## 5. АРХИТЕКТУРА AUTOMATCH 2

Cargo-воркспейс; каждый модуль — крейт или модуль с чистыми границами:

```
automatch2/
  Cargo.toml          (workspace)
  shaders/            (GLSL: копии из v1 + новые, дорабатываем тут)
  crates/ (или src/ с модулями)
    core/             Engine, RenderGraph, FBO-пул, GlTexture, часы/время
    gfx/              Shader, Program, Pass, UniformBuffer, PingPong (glow)
    effects/          трейт Effect + реестр; каждый эффект = тип + .frag
    media/            MediaIO (decode→GPU), Encoder, ClipPool, prefetch
    audio/            AudioAnalysis (aubio/rustfft), BeatGrid, спектр/огибающие
    timeline/         Timeline, Cut, TransitionEngine, beat-sync builder
    modmatrix/        ModMatrix (LFO + аудио-огибающие → параметры)
    state/            Params/Config (отражает state.py), пресеты, сессии (serde)
    ui/               egui-приложение: viewport, node-editor, timeline, panels, theme
    datamosh/         нативный битстрим-мош (порт datamosh_bitstream.py на ffmpeg-next)
    app/              main(), связывание, CLI
  tests/              минимальные проверки (golden-кадры, парсер AVI и т.п.)
```

**Поток данных (render):** ClipPool → кадр в GL-текстуру (PBO upload) → RenderGraph
(ping-pong FBO, проход на эффект) → финальный composite → (preview: на экран через egui_glow |
export: PBO download → Encoder). Параметры эффектов — в UBO, обновляются модуляцией пер-кадр.

**Оптимизация (с самого начала):**
- Кадры живут на GPU; download только на экспорт.
- Пул FBO/текстур, переиспользование, без аллокаций в hot-loop.
- PBO для async upload/download.
- Потоки: [decode/prefetch] → [render] → [encode], очереди с backpressure.
- Кэш анализа аудио и таймлайна; детерминизм по seed (как в v1).

---

## 6. КАТАЛОГ ЭФФЕКТОВ (разноплановые, виральные — НЕ квадратики/перекрашивания)

Легенда: ⭐ = виральный приоритет · ⮕ = порт из v1 (дорабатываем) · ✦ = новый.

**A. Датамош и компрессия**
- ⭐⮕ Битстрим-датамош (bloom/smear) — нативно через libav.
- ⭐✦ Оптик-флоу мош (GPU): смещение кадра по оценённым векторам движения — мош в реальном времени в превью.
- ⭐✦ Pixel drag / smear-stretch: «протягивание» строки/столбца пикселей вдоль движения.
- ✦ Freeze + motion-only update (обновляется только движущееся).

**B. Время**
- ⭐✦ Time displacement / time-slice: кольцевой буфер кадров, задержка по строке/столбцу → радужные «срезы времени».
- ⭐✦ Echo / onion-skin трейлы (взвешенная история кадров).
- ⭐✦ Beat-stutter / strobe / frame-hold, синхронно с битами.
- ✦ Speed-ramp / тайм-ремап на бит.
- ⭐✦ Feedback-zoom тоннель (Droste-ish бесконечный зум).

**C. Оптика / линза**
- ⭐✦ Хроматическая дисперсия (радиальная призма линзы), анаморфные блики.
- ⭐✦ Light leaks + анаморфные стрики (киношный засвет).
- ✦ Tilt-shift / DOF (через депт).

**D. Дисторшн / варп**
- ⭐✦ Flow-field domain warp (curl-noise) — жидкий «melt».
- ⭐✦ Liquify / displacement по шуму и по аудио.
- ✦ Rolling shutter / jelly-wobble.
- ⭐✦ Shockwave / ripple на бит.
- ✦ Glass/refraction преломление.

**E. Сигнал / аналог (ретро, виральное)**
- ⭐✦ VHS глубокий: tracking error, head-switch шум, chroma bleed, dropout, tape warble, time-base error.
- ⮕ CRT: barrel + scanlines + aperture grille + phosphor persistence + rolling bars (из `crt_monitor.frag`).
- ⭐✦ Broadcast signal loss: sync tear, horizontal-hold roll, снег, сбой цветовой поднесущей.

**F. Геометрия / зеркала**
- ⮕ Калейдоскоп (n-fold), зеркала.
- ⭐✦ Droste рекурсивный зум, фрактальный feedback.

**G. Стилизация (умеренно — не злоупотреблять перекрашиванием)**
- ⭐✦ Neon edge / контурное свечение (sobel → bloom).
- ✦ Halftone / newsprint / dot-screen.
- ⮕ ASCII / character-mapping (динамический).
- ⮕ (низкий приоритет) duotone/posterize — оставляем минимум, не плодим.

**H. Депт / 3D (стретч, есть depth_anything)**
- ⭐✦ Depth-parallax / фейковое 3D-движение камеры.
- ✦ Анаглиф 3D, depth-fog.
- ⭐✦ Depth-gated глитч (искажаем только передний/задний план).

**I. Аудио-реактив (виральное)**
- ⭐✦ RGB-shake / zoom-punch / scale на транзиентах.
- ⭐✦ Spectrum-driven warp (искажение по полосам спектра).
- ✦ Вспышки/частицы на бит (наследуем модуляцию v1).

**J. Плёночные дефекты**
- ✦ Реалистичное зерно (многооктавное), пыль, царапины, волоски, gate-weave, фликер.

Правило приёмки эффекта: **выглядит круто на реальном видео и заметно отличается от соседних**.
Если эффект «ещё одни квадратики/перекраска» — не добавляем.

---

## 7. РЕФЕРЕНСЫ ДЛЯ ПОИСКА (GitHub / ShaderToy / стартапы)

Перед фазами эффектов и UI ищу эталоны (записывать найденное сюда же):

- Глитч-шейдеры: `ofxGlitch`, `v002` (vade), `hbbtstar/glsl-glitch`, ShaderToy «datamosh / time displacement / RGB shift / feedback».
- Датамош: `ucnv/datamosh`, `itsKaspar`, avant-garde AVI-байт-скрипты (сверить наш парсер).
- Live-visuals идеи: `Hydra` (ojack/hydra), Milkdrop/`Butterchurn`, `TouchDesigner` примеры, Resolume.
- Beat/onset: `aubio/aubio`, `Spleeter`-free альтернативы для битов.
- UI (Rust): `emilk/egui`, `egui_node_graph`, `egui_plot`, `grovesNL/glow`, примеры node-graph редакторов (шейдер-граф).
- Видео-движки/идеи: `bkaradzic/bgfx` (ориентир архитектуры), `processing`/`openFrameworks` аддоны, Rust: `ffmpeg-next`, `aubio`-rs, `rustfft`.
- Оптический поток на GPU: реализации Farneback/HS на GLSL, NVIDIA Optical Flow (опц.).
- Рынок/стартапы: какие фишки у TikTok CapCut эффектов, Kaiber, Runway (глитч-стиль) — что взять как планку.

---

## 8. ДОРОЖНАЯ КАРТА ПО ФАЗАМ (с проверкой и чекпоинтами)

Каждая фаза: **Цель → Задачи → Критерий готовности (verify) → Чекпоинт (коммит+push).**

### Phase 0 — Research & Scaffolding
- R1: собрать референсы (§7), записать выводы и 3 «планки» по UI и эффектам.
- S1: `cargo` workspace + `Cargo.toml` (winit, glutin, glow, egui, egui_glow, egui_plot, serde, serde_json, image; позже ffmpeg-next, aubio/rustfft).
- S2: пустое окно winit + GL 4.x контекст (glutin/glow) + egui «Hello» с панелью.
- **Verify:** `cargo run` открывает окно, рисует egui-панель, чистый выход, FPS-счётчик.
- **Checkpoint C0:** `automatch2: scaffold (winit+glow+egui+cargo)`.

### Phase 1 — Core GFX engine
- Программы/шейдеры (загрузка, кэш юниформов), fullscreen-quad, FBO-пул, ping-pong, GLTexture.
- Один тестовый проход (passthrough) кадра-картинки на экран.
- **Verify:** загруженный PNG проходит через 1 GLSL-проход и отображается без артефактов.
- **Checkpoint C1.**

### Phase 2 — Media I/O (decode)
- ClipPool: декод видео в GL-текстуру. Старт через ffmpeg CLI → rawvideo пайп (как v1), upload по PBO.
- Базовый плеер: кадры идут, time-base корректный.
- **Verify:** реальный mp4 проигрывается в окне на нужном FPS, без утечек памяти.
- **Checkpoint C2.**

### Phase 3 — Effect graph + порт первых шейдеров
- Трейт `Effect` + реестр; перенести 6–8 шейдеров из v1 (rgb_split, wave_warp, crt_monitor, motion_trails, pixel_sort, datamosh-GPU, kaleidoscope, dither).
- Линейная цепочка эффектов с mix/blend (как `EffectConfig`).
- **Verify:** включение/выключение/микс каждого эффекта меняет картинку как в v1; сравнение «на глаз» с эталоном v1.
- **Checkpoint C3.**

### Phase 4 — Audio + Timeline (beat-sync)
- AudioAnalysis на aubio: BPM, биты, даунбиты, огибающие/спектр.
- Timeline builder (порт логики `build_timeline`), нарезка по битам, seed-детерминизм.
- ModMatrix (LFO + аудио → параметры).
- **Verify:** на реальном треке биты совпадают со слухом; авто-нарезка делает монтаж, синхронный с музыкой.
- **Checkpoint C4.**

### Phase 5 — Export + нативный датамош
- Encoder: кадры с GPU → ffmpeg (сначала CLI-пайп), мукс аудио.
- Порт `datamosh_bitstream.py` на Rust (ffmpeg-next/libav): bloom/smear/fine-scale.
- **Verify:** экспортный mp4 играбелен, длительность синхронна музыке; датамош виден в файле и совпадает по логике с v1.
- **Checkpoint C5.**

### Phase 6 — UI overhaul (максимально)
- egui-док: Viewport (живое превью) | Node-graph эффектов (egui_node_graph) | Timeline (волна+биты+лейны) | Параметры | Браузер пресетов.
- Ретро-глитч тема (моно-шрифт, CRT-акценты), плавность, горячие клавиши.
- Модуляция визуально (кривые/маршруты), превью-миниатюры эффектов.
- **Verify:** можно собрать монтаж мышью от и до (выбор медиа → граф → таймлайн → экспорт) без правки кода.
- **Checkpoint C6.**

### Phase 7 — Большой эффект-пуш (разноплановые виральные)
- Реализуем новые эффекты из §6 группами (B время, C оптика, D варп, E сигнал, I аудио-реактив, F Droste, A оптик-флоу мош, H депт).
- Каждый эффект: шейдер + параметры + пресет + проверка на реальном видео.
- **Verify (на каждый):** «круто и отличается». Ведём список готовых в §9.
- **Checkpoint C7.x** (по группе эффектов).

### Phase 8 — Оптимизация
- Потоки decode/render/encode, очереди, prefetch; PBO async; пул ресурсов; профайл GPU/CPU.
- **Verify:** целевой FPS превью на 1080p с тяжёлой цепочкой; экспорт быстрее реалтайма; нет роста памяти.
- **Checkpoint C8.**

### Phase 9 — Полировка и упаковка
- Пресеты «из коробки» (виральные луки), онбординг, сборка/инсталлятор, README, демо-клип.
- **Verify:** чистая установка на пустой системе запускается и рендерит демо.
- **Checkpoint C9 — v2.0.0 tag.**

---

## 9. ЧЕКЛИСТ ЧЕКПОИНТОВ И ЭФФЕКТОВ

**Фазы:**
- [x] C0 Scaffold (cargo + winit+glow+egui окно) — `e841b8e` build OK, окно запускается
- [ ] C1 Core GFX (ping-pong, passthrough)
- [ ] C2 Media decode → GL
- [ ] C3 Effect graph + порт шейдеров
- [ ] C4 Audio + timeline (beat-sync)
- [ ] C5 Export + нативный датамош
- [ ] C6 UI overhaul
- [ ] C7 Эффект-пуш (группы ниже)
- [ ] C8 Оптимизация
- [ ] C9 Полировка + v2.0.0

**Эффекты (отмечаем по мере готовности + проверки на реальном видео):**
- [ ] Битстрим-датамош (native) · [ ] Оптик-флоу мош · [ ] Pixel drag/stretch
- [ ] Time displacement · [ ] Echo-трейлы · [ ] Beat-stutter/strobe · [ ] Feedback-zoom Droste
- [ ] Хром. дисперсия · [ ] Light leaks/анаморф · [ ] Tilt-shift(depth)
- [ ] Flow-field warp · [ ] Liquify(audio) · [ ] Rolling shutter · [ ] Shockwave-ripple
- [ ] VHS deep · [ ] CRT(порт) · [ ] Broadcast signal loss
- [ ] Калейдоскоп(порт) · [ ] Droste/фрактал-feedback
- [ ] Neon edge · [ ] Halftone · [ ] ASCII(порт)
- [ ] Depth-parallax · [ ] Анаглиф · [ ] Depth-gated глитч
- [ ] RGB-shake/zoom-punch · [ ] Spectrum warp · [ ] Зерно/пыль/царапины

---

## 10. РИСКИ И РЕШЕНИЯ

- **libav/ffmpeg-next сложен** → фазируем: сначала ffmpeg-CLI пайп (быстро рабочее), потом нативный `ffmpeg-next`.
- **Оптический поток дорогой** → начать с дешёвой GLSL-аппроксимации (block-matching/HS), при нужде NVIDIA OF.
- **egui ≠ Dear ImGui по стилю** → темизуем egui под ретро-глитч; node-graph через `egui_node_graph`.
- **Незнакомый GPU-interop на Rust** → маленькие шаги с проверкой; glow повторяет семантику moderngl из v1.
- **Сравнение с v1** → держать v1 рядом как эталон «как должно выглядеть» (НЕ трогая его файлы).
- **Расползание объёма** → строго «кусок→проверка→коммит», маленькие шаги, чекбоксы в §9.
- **Кросс-платформа** → cargo кросс-платформен из коробки, но цель №1 — Windows.

---

## 11. ЖУРНАЛ (дописывать снизу, кратко: дата — что сделано — verify — коммит)

- 2026-06-14 — План создан (`automatch2/PLAN.md`). Структура, стек, каталог эффектов, дорожная карта, чеклист зафиксированы. Verify: n/a (документ). Коммит: `6c4df20`.
- 2026-06-14 — Решение по стеку: **Rust + glow + egui** (вместо C++). Причина — дословный перенос GLSL-шейдеров (glow=OpenGL) + безопасность/cargo. Обновлены §3–§5, §7–§8, §10. wgpu отложен (требует WGSL). Verify: n/a (документ).
- 2026-06-14 — **C0 (Phase 0) ГОТОВ.** Поставлен Rust (rustup, stable-msvc). Создан cargo-скелет `automatch2/` на `eframe` (winit+glutin+glow+egui): окно «automatch 2 — C0 scaffold», egui-панель показывает версию OpenGL и FPS. Решено брать GL-контекст через `eframe::glow` и рисовать кастомный GL в вьюпорт через `egui::PaintCallback`+`egui_glow::CallbackFn`. Verify: `cargo build` Finished (1m21s); бинарник запущен — окно жило 8с без падения. Файлы: `Cargo.toml`, `src/main.rs`, `.gitignore` (игнор `/target`).
