# AnalogVFX

Аналоговые искажения видео в реальном времени с USB-камеры → Spout → TouchDesigner / OBS / Resolume Arena.

Пайплайн на GPU (GLSL):
**VHS → RGB-split → Glitch/datamosh → CRT/scanlines → Drip (стекание кадров вниз) → Spout sender**

## Установка

```powershell
conda create -n analogvfx python=3.10 -y
conda activate analogvfx
cd C:\Users\divin\Code\analogvfx
pip install -r requirements.txt
python main.py
```

## Использование

1. `python main.py` — открывается панель управления и окно превью.
2. Выбери камеру в **Camera**, при необходимости задай **Resolution**.
3. Жми **Старт**. В окне превью пойдёт обработанный сигнал.
4. Крути слайдеры — изменения видны на лету.

Текущая Spout-сессия называется `AnalogVFX` (можно сменить в поле **Spout sender**).

## Подключение к приёмникам

| Приложение | Источник |
| --- | --- |
| **TouchDesigner** | `Spout In TOP`, `Sender = AnalogVFX` |
| **OBS** | Установить плагин [obs-spout2-plugin](https://github.com/Off-World-Live/obs-spout2-plugin) → Source `Spout2 Capture` |
| **Resolume Arena** | `Sources → Spout`, перетащить `AnalogVFX` на слой |

Запись финального видео делается на стороне OBS (Spout → запись), TD (Movie File Out TOP) или Arena.

## Пресеты

Кнопки `Save preset` / `Load preset` сохраняют все параметры эффектов в JSON. Загрузка восстанавливает положение слайдеров.

## Структура

```
main.py        — точка входа, запускает 3 потока
state.py       — общее состояние (Params dataclass + locks)
capture.py     — поток захвата с USB (OpenCV / DirectShow)
renderer.py    — GLFW + moderngl, цепочка FBO, ping-pong feedback, Spout
gui.py         — Dear PyGui панель управления
shaders/       — GLSL: vhs / rgb_split / glitch / crt / drip / final
```

## Troubleshooting

- **`SpoutGL not installed`** в консоли — Spout-выход выключен, превью всё равно работает. Поставь `pip install SpoutGL`.
- **Чёрный экран в превью, но капчер шумит** — проверь, не блокирует ли камеру другое приложение (OBS, Zoom). На Windows одна камера = один потребитель в каждый момент.
- **30+ FPS не держится** — снизь Resolution до 720p, ослабь Drip (Decay ближе к 0.9 и ниже).
- **Spout приёмник видит перевёрнутую картинку** — в `renderer.py` поменяй `True` на `False` в вызове `spout.sendTexture(..., True, 0)` (флаг bInvert).
