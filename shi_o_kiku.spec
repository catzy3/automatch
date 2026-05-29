# -*- mode: python ; coding: utf-8 -*-
# ──────────────────────────────────────────────────────────────────────────────
#  shi o kiku  //  alfa 1.0
#  PyInstaller one-directory spec
#
#  Build:  pyinstaller shi_o_kiku.spec --clean
#  Output: dist\shi_o_kiku\shi_o_kiku.exe
#
#  Python: C:\Python312   PyInstaller: 6.20.0   Platform: win-amd64
# ──────────────────────────────────────────────────────────────────────────────

import os

_SP = r'C:\Python312\Lib\site-packages'


# ── small model files that ship with the build ─────────────────────────────
# depth_anything_v2_small.onnx (~95 MB) is excluded — needs onnxruntime which
# is excluded from the bundle. Copy it manually next to the .exe if needed.
_MODEL_DATAS = []
for _m in ('ascii_atlas.png', 'face_detection_yunet_2023mar.onnx'):
    _p = os.path.join('models', _m)
    if os.path.exists(_p):
        _MODEL_DATAS.append((_p, 'models'))


a = Analysis(
    ['shi_o_kiku.py'],
    pathex=['.'],

    # ── native binaries (DLLs loaded at runtime) ───────────────────────────
    binaries=[
        # PortAudio — sounddevice looks here relative to _sounddevice_data pkg
        (os.path.join(_SP, r'_sounddevice_data\portaudio-binaries\libportaudio64bit.dll'),
         '_sounddevice_data/portaudio-binaries'),
        (os.path.join(_SP, r'_sounddevice_data\portaudio-binaries\libportaudio64bit-asio.dll'),
         '_sounddevice_data/portaudio-binaries'),
        # libsndfile — soundfile looks here relative to _soundfile_data pkg
        (os.path.join(_SP, r'_soundfile_data\libsndfile_x64.dll'),
         '_soundfile_data'),
        # GLFW — glfw python pkg loads glfw3.dll from its own package dir
        (os.path.join(_SP, r'glfw\glfw3.dll'),    'glfw'),
        (os.path.join(_SP, r'glfw\msvcr120.dll'), 'glfw'),
        # DearPyGui CRT
        (os.path.join(_SP, r'dearpygui\vcruntime140_1.dll'), 'dearpygui'),
    ],

    # ── data files ─────────────────────────────────────────────────────────
    datas=[
        # GLSL shaders — renderer.py finds them via Path(__file__).parent/shaders
        ('shaders', 'shaders'),
        # FFmpeg exe — imageio_ffmpeg.get_ffmpeg_exe() looks in its binaries/ dir
        (os.path.join(_SP, r'imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe'),
         'imageio_ffmpeg/binaries'),
    ] + _MODEL_DATAS,

    # ── hidden imports (dynamic imports + packages PyInstaller might miss) ──
    hiddenimports=[
        # GUI / GL
        'dearpygui',
        'dearpygui.dearpygui',
        'moderngl',
        'glfw',
        # Computer vision
        'cv2',
        # Audio
        'sounddevice',
        'soundfile',
        '_sounddevice_data',
        '_soundfile_data',
        # Video / encoding
        'imageio_ffmpeg',
        # Image processing
        'PIL',
        'PIL.Image',
        'PIL.ImageFont',
        'PIL.ImageDraw',
        # Networking (Pexels downloader)
        'requests',
        'requests.adapters',
        'requests.auth',
        'urllib3',
        'certifi',
        'charset_normalizer',
        # Standard / UI
        'tkinter',
        'tkinter.messagebox',
        # ── local app modules ─────────────────────────────────────────
        'state',
        'automatch',
        'automatch_gui',
        'audio',
        'modulation',
        'smart_random',
        'ascii_atlas',
        'renderer',
        'capture',
        'detection',
        'overlay',
        'recorder',
        'depth',
        'stickers',
        'gui',
        'main',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],

    # ── excludes: keep the bundle lean ─────────────────────────────────────
    # SpoutGL  — only useful in dev with Spout server running
    # mediapipe / onnxruntime — optional heavy deps; user can install manually
    # Everything below is from the ML environment and must NOT ship
    excludes=[
        # ── GUI toolkits we don't use ───────────────────────────────────
        'SpoutGL',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
        # ── optional / heavy optional deps ─────────────────────────────
        'mediapipe',
        'onnxruntime',
        # ── ML / data-science stack (not used by this app) ─────────────
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'bitsandbytes',
        'transformers', 'tokenizers', 'datasets', 'huggingface_hub',
        'accelerate', 'diffusers',
        'sklearn', 'scikit_learn', 'scikit-learn',
        'pyarrow', 'parquet',
        'hf_xet', 'safetensors',
        'pydantic', 'pydantic_core',
        'lxml',
        # ── numeric / scientific (heavy, not needed) ────────────────────
        'matplotlib', 'scipy', 'pandas',
        'numba', 'llvmlite',
        'statsmodels', 'sympy',
        # ── media / download tools ──────────────────────────────────────
        'yt_dlp',
        'mutagen',
        'brotli',
        'curl_cffi',
        # ── crypto / auth (not used) ────────────────────────────────────
        'cryptography', 'secretstorage', 'keyring',
        # ── async / web frameworks ───────────────────────────────────────
        'websockets', 'aiohttp', 'fastapi', 'uvicorn', 'starlette',
        # ── dev / test ───────────────────────────────────────────────────
        'IPython', 'jupyter', 'notebook', 'ipykernel',
        '_pytest', 'pytest',
        # ── misc installers ──────────────────────────────────────────────
        'setuptools', 'pip', 'pkg_resources',
    ],

    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='shi_o_kiku',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX off — DearPyGui .pyd breaks with UPX
    console=False,           # no cmd window; crash.log handles errors
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # set to .ico path if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='shi_o_kiku',
)
