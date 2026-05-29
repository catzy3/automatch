@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

:: Always run from the script's own directory so relative paths work
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   shi o kiku  //  alfa 1.0               ║
echo  ║   build script                           ║
echo  ╚══════════════════════════════════════════╝
echo.

set "PYTHON=C:\Python312\python.exe"
set "SPEC=shi_o_kiku.spec"
set "DIST=dist\shi_o_kiku"

:: ── preflight ──────────────────────────────────────────────────────────────
if not exist "%PYTHON%" (
    echo [ERROR] Python not found at %PYTHON%
    goto :fail
)
if not exist "%SPEC%" (
    echo [ERROR] %SPEC% not found. Run this from the project root.
    goto :fail
)

:: ── kill any leftover shi_o_kiku process (locks the build exe) ────────────
taskkill /f /im shi_o_kiku.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: ── wipe old build dirs so --clean never hits a locked file ───────────────
if exist "build\shi_o_kiku" rd /s /q "build\shi_o_kiku"
if exist "dist\shi_o_kiku"  rd /s /q "dist\shi_o_kiku"

:: ── run PyInstaller ────────────────────────────────────────────────────────
echo [1/3] Running PyInstaller...
echo.
"%PYTHON%" -m PyInstaller "%SPEC%"
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed.
    goto :fail
)

:: ── post-build: create writable directories next to the exe ────────────────
echo.
echo [2/3] Creating runtime directories...

for %%d in (recordings media_cache models) do (
    if not exist "%DIST%\%%d" (
        mkdir "%DIST%\%%d"
        echo        created: %DIST%\%%d
    )
)

:: ── post-build: copy pexels config if present (never overwrite) ─────────────
echo.
echo [3/3] Copying user config...

if exist "pexels_config.json" (
    if not exist "%DIST%\pexels_config.json" (
        copy /Y "pexels_config.json" "%DIST%\pexels_config.json" >nul
        echo        copied: pexels_config.json
    ) else (
        echo        skipped: pexels_config.json already in dist (not overwriting)
    )
) else (
    echo        note: no pexels_config.json found; Pexels download won't work until key is set
)

:: ── remove intermediate build dir so the wrong exe can't be launched ──────
rd /s /q "build\shi_o_kiku" >nul 2>&1

:: ── done ──────────────────────────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   BUILD COMPLETE                         ║
echo  ╚══════════════════════════════════════════╝
echo.
echo   Exe:   %DIST%\shi_o_kiku.exe
echo   Size:  (check dist\shi_o_kiku folder)
echo.
echo   Optional: copy models\depth_anything_v2_small.onnx
echo             to %DIST%\models\ for depth-mask support.
echo.
pause
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
