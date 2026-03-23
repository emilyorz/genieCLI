@echo off
title TGenie CLI

:: �w�w �]�w�ϡ]���ݭק�^�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w
set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
set CHROME_PROFILE=C:\ChromeDebug
set SCRIPT_DIR=%~dp0
set PYTHON=python
:: �w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w�w

echo.
echo  +======================================+
echo  ^|        TGenie CLI  Launcher          ^|
echo  +======================================+
echo.

:: 1. �T�{ Chrome �s�b
if not exist %CHROME% (
    echo  [ERROR] Chrome not found: %CHROME%
    echo  Please update CHROME path in this bat file.
    pause
    exit /b 1
)

:: 2. �T�{ Python �s�b
%PYTHON% --version > nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Make sure python is in PATH.
    pause
    exit /b 1
)

:: 3. �T�{ Chrome �O�_�w�g�b debug mode �]
curl -s http://localhost:9222/json > nul 2>&1
if errorlevel 1 (
    echo  Starting Chrome in debug mode...
    start "" %CHROME% ^
        --remote-debugging-port=9222 ^
        --user-data-dir="%CHROME_PROFILE%" ^
        --remote-allow-origins=http://localhost:9222

    echo  Waiting for Chrome to start...
    timeout /t 3 /nobreak > nul

    :: �A�� Chrome �u���_��
    :wait_chrome
    curl -s http://localhost:9222/json > nul 2>&1
    if errorlevel 1 (
        timeout /t 1 /nobreak > nul
        goto wait_chrome
    )
    echo  [OK] Chrome is ready
) else (
    echo  [OK] Chrome already running on port 9222
)

echo.
echo  Please make sure you are logged into TGenie in the Chrome window.
echo  Press any key when ready...
pause > nul

:: 4. �� auth token
echo.
echo  Grabbing auth token...
%PYTHON% "%SCRIPT_DIR%grab_auth.py"
if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to grab auth. See error above.
    pause
    exit /b 1
)

:: 5. �i�J AI agent
echo.
%PYTHON% "%SCRIPT_DIR%main.py" chat --skills

echo.
pause
