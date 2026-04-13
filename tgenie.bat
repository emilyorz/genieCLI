@echo off
title GenieCLI — Trino Query Tuning

set SCRIPT_DIR=%~dp0
set PYTHON=python

echo.
echo  +======================================+
echo  ^|  GenieCLI — Trino Query Tuning       ^|
echo  +======================================+
echo.

:: 1. Check Python
%PYTHON% --version > nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Make sure python is in PATH.
    pause
    exit /b 1
)

:: 2. Detect interface from config
%PYTHON% -c "import json,pathlib; p=pathlib.Path.home()/'ai-agent-config.json'; d=json.loads(p.read_text()) if p.exists() else {}; print(d.get('interface','tgenie'))" > %TEMP%\genie_iface.txt 2>nul
set /p INTERFACE=<%TEMP%\genie_iface.txt
del %TEMP%\genie_iface.txt 2>nul

:: 3. TGenie auth (needs Chrome for grab_auth.py)
if "%INTERFACE%"=="tgenie" (
    set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
    set CHROME_PROFILE=C:\ChromeDebug

    curl -s http://localhost:9222/json > nul 2>&1
    if errorlevel 1 (
        if exist %CHROME% (
            echo  Starting Chrome for TGenie auth...
            start "" %CHROME% ^
                --remote-debugging-port=9222 ^
                --user-data-dir="%CHROME_PROFILE%" ^
                --remote-allow-origins=http://localhost:9222
            timeout /t 3 /nobreak > nul
        ) else (
            echo  [WARN] Chrome not found — run 'genie setup' to use a different backend.
        )
    )

    echo.
    echo  Please log into TGenie in Chrome, then press any key...
    pause > nul

    echo  Grabbing auth token...
    %PYTHON% "%SCRIPT_DIR%grab_auth.py"
    if errorlevel 1 (
        echo  [WARN] Auth grab failed — use /renew inside CLI.
    )
) else (
    echo  [OK] Using %INTERFACE% interface
)

:: 4. Launch CLI
echo.
%PYTHON% -m genie chat %*

echo.
pause
