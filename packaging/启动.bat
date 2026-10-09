@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title Malice-Library

echo ==========================================
echo   Malice-Library - one-click launcher
echo ==========================================
echo.

cd /d "%~dp0"

rem --- optional override: set PY_CMD to python.exe full path ---
if not defined PY_CMD (
    set "PY_CMD="
)

rem --- 1: try py launcher ---
if not defined PY_CMD (
    py -3 -c "pass" >nul 2>&1
    if !errorlevel!==0 set "PY_CMD=py -3"
)

rem --- 2: try python command, guard against MS Store stub ---
if not defined PY_CMD (
    python -c "pass" >nul 2>&1
    if !errorlevel!==0 set "PY_CMD=python"
)

rem --- 3: try python3 ---
if not defined PY_CMD (
    python3 -c "pass" >nul 2>&1
    if !errorlevel!==0 set "PY_CMD=python3"
)

rem --- 4: search common install folders ---
if not defined PY_CMD (
    for %%D in ("%LOCALAPPDATA%\Programs\Python" "C:\Program Files\Python" "C:\Python") do (
        if not defined PY_CMD (
            if exist "%%~D" (
                for /r "%%~D" %%P in (python.exe) do (
                    if not defined PY_CMD (
                        "%%P" -c "pass" >nul 2>&1
                        if !errorlevel!==0 set "PY_CMD=%%P"
                    )
                )
            )
        )
    )
)

if not defined PY_CMD (
    echo [FAIL] A working Python 3.10+ was not found.
    echo.
    echo Tips:
    echo   - Install from https://www.python.org/downloads/ and check Add python.exe to PATH.
    echo   - If typing python opens the Microsoft Store,
    echo     disable its app execution aliases:
    echo     Settings - Apps - Advanced app settings - App execution aliases
    echo     then turn OFF entries named python.exe and python3.exe.
    echo   - Or set PY_CMD manually at the top of this file:
    echo     set "PY_CMD=C:\Python\python.exe"
    pause
    exit /b 1
)

echo Using Python: %PY_CMD%
%PY_CMD% --version
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual env ...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [FAIL] venv create failed.
        pause
        exit /b 1
    )
)

echo [2/3] Installing dependencies ...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [FAIL] pip install failed.
    pause
    exit /b 1
)

echo [2.5/3] Ensuring Playwright Chromium ...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Chromium install failed - album browsing may be limited.
)

echo [3/3] Starting server ...
echo.
echo   Open: http://127.0.0.1:5000
echo   Close this window to stop.
echo.
start "" "http://127.0.0.1:5000"
.venv\Scripts\python.exe app.py
pause