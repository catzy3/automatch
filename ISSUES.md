# Known Issues

## [UNRESOLVED] Effect Browser preview — GL context conflict

**Date:** 2026-05-31  
**Status:** Unresolved — browser removed from codebase

### Problem

`effect_preview.generate_preview()` calls `moderngl.create_standalone_context()` internally.  
When called from within DPG's event loop (main thread), this conflicts with DPG's own active GLFW/WGL context — the standalone context either fails to initialize or causes a GLFWError (`WGL: Failed to clear current context`).

Moving generation to a background thread avoids the main-thread GL conflict, but the result then needs to be uploaded to a DPG dynamic texture from the **main thread** (DPG is not thread-safe). The frame-callback polling approach (`set_frame_callback`) adds latency and complexity without being reliable.

### Root cause

Two OpenGL contexts cannot safely share state on the same thread on Windows WGL.  
DPG owns the primary context; moderngl standalone context is incompatible with it at runtime.

### Potential future solution

- Pre-generate all previews **before** `dpg.create_context()` (at import time or via a CLI pre-bake step), save as `.npy` files, load into DPG static textures at startup.  
- Or: use a **separate process** (not thread) for rendering, communicate via shared memory / pipes.
