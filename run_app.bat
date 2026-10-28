@echo off
REM ---------------------------------------------------------------
REM BridgeStudio (NL-FE preprocessor) launcher
REM NOTE: this file is intentionally ASCII-only.
REM Chinese text in a .bat gets decoded as GBK by cmd.exe, and the
REM resulting mojibake can contain quotes/pipes that break commands.
REM ---------------------------------------------------------------
cd /d "D:\AI-work\BridgeStudio"

REM ---- Optional: LLM parsing ----
REM The key lives in set_key.bat, which is git-ignored (never committed).
REM Without that file the built-in rule parser is used (offline, no cost).
if exist "%~dp0set_key.bat" call "%~dp0set_key.bat"

set "PORT=8501"
set "EXE=C:\Users\Thinkpad\.workbuddy\binaries\python\envs\default\Scripts\streamlit.exe"

if not exist "%EXE%" (
    echo [ERROR] streamlit.exe not found:
    echo   %EXE%
    echo Create the venv first, or fix the path in this script.
    pause
    exit /b 1
)

REM ---- Kill any stale process holding the port -------------------
REM (redirect to a temp file first: avoids pipe-escaping issues
REM  inside the for /f command string)
netstat -ano | findstr ":%PORT%" | findstr LISTENING > "%TEMP%\nlfe_port.txt"
for /f "usebackq tokens=5" %%p in ("%TEMP%\nlfe_port.txt") do (
    echo Killing stale process on port %PORT%, PID=%%p
    taskkill /PID %%p /F >nul 2>&1
)
del "%TEMP%\nlfe_port.txt" >nul 2>&1
timeout /t 1 >nul

echo Starting NL-FE preprocessor ...
echo URL: http://localhost:%PORT%
echo (browser opens automatically in a few seconds)

start "" /b cmd /c "timeout /t 4 >nul & start "" http://localhost:%PORT%"

"%EXE%" run app.py --server.port %PORT% --server.headless true
pause
