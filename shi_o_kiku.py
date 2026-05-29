"""
shi o kiku  //  alfa 1.0
死を聞く — "to hear death"

Beat-sync auto editor.  Entry point for both source-run and frozen .exe.
"""
import sys
import os
import traceback
from pathlib import Path

APP_NAME    = "shi o kiku"
APP_VERSION = "alfa 1.0"
APP_BUILD   = "2026-05-26"


# ── path resolution: works both in dev (python shi_o_kiku.py)
#    and in frozen .exe (PyInstaller one-directory bundle) ─────────────────────
def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


APP_DIR = _app_dir()

# ── make sure we can find our own modules when running from source ────────────
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(APP_DIR))


# ── redirect stderr → crash log next to the exe so users can report bugs ─────
_LOG_PATH = APP_DIR / "crash.log"


def _install_crash_handler():
    import io

    class _Tee:
        def __init__(self, *streams):
            self._s = streams
        def write(self, data):
            for s in self._s:
                try: s.write(data)
                except Exception: pass
        def flush(self):
            for s in self._s:
                try: s.flush()
                except Exception: pass

    try:
        log_file = open(_LOG_PATH, "a", encoding="utf-8", buffering=1)
        log_file.write(f"\n{'='*60}\n{APP_NAME} {APP_VERSION}  {APP_BUILD}\n")
        sys.stderr = _Tee(sys.stderr, log_file)
    except Exception:
        pass


_install_crash_handler()


# ── runtime directory setup ───────────────────────────────────────────────────
for _d in ("recordings", "models"):
    (APP_DIR / _d).mkdir(parents=True, exist_ok=True)


# ── patch automatch_gui branding + paths ─────────────────────────────────────
try:
    import automatch_gui as _gui
    _gui.APP_NAME    = APP_NAME
    _gui.APP_VERSION = APP_VERSION
    _gui.APP_DIR     = APP_DIR
    _gui.STATE["output"] = str(APP_DIR / "recordings" / "automatch.mp4")
except Exception:
    pass


# ── console startup banner (ASCII eye logo) ──────────────────────────────────
def _print_banner():
    try:
        from logo_ascii import ASCII_LOGO
        print(ASCII_LOGO)
        print(f"  {APP_NAME}  //  {APP_VERSION}     beat-sync auto editor")
        print()
    except Exception:
        pass


# ── launch ────────────────────────────────────────────────────────────────────
def main():
    _print_banner()
    try:
        import automatch_gui
        return automatch_gui.main()
    except Exception:
        # If the GUI crashes, write the traceback to crash.log and,
        # if there's a console, print it there too.
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        # show a minimal tkinter error dialog so the user isn't left staring
        # at nothing in no-console builds
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                f"{APP_NAME}  {APP_VERSION} — crash",
                f"Unhandled error:\n\n{tb[-1200:]}\n\nSee crash.log for details.",
            )
            root.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
