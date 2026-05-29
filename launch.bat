@echo off
chcp 65001 >nul
set "PYTHON=C:\Python312\python.exe"

if not exist "%PYTHON%" (
    echo Python not found at %PYTHON%
    pause & exit /b 1
)

"%PYTHON%" shi_o_kiku.py %*
