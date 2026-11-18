@echo off
REM ---------------------------------------------------------------
REM BridgeStudio (NL-FE preprocessor) launcher - portable/cross-machine
REM NOTE: this file is intentionally ASCII-only. cmd.exe decodes .bat
REM as GBK; non-ASCII bytes can turn into quotes/pipes and break commands.
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

REM ---- Optional: LLM parsing key (git-ignored) -------------------
if exist "%~dp0set_key.bat" call "%~dp0set_key.bat"

set "PORT=8501"
set "MPLBACKEND=Agg"

REM ---- Locate a Python that can actually import Streamlit --------
REM Priority: local venv > bundled portable runtime > WorkBuddy venv
REM           > every Python on PATH > py launcher.
REM Every candidate is verified with the required imports, so an
REM interpreter with missing packages is skipped instead of failing.
set "PY="
set "FIRST_PY="
call :try_py "%~dp0.venv\Scripts\python.exe"
if not defined PY call :try_py "%~dp0runtime\python.exe"
if not defined PY call :try_py "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PY call :try_py "%%i"
    )
)
if not defined PY (
    for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
        if not defined PY call :try_py "%%i"
    )
)

if not defined PY (
    echo [ERROR] No Python with the required packages found.
    echo Need: streamlit, anastruct, numpy, matplotlib
    if defined FIRST_PY echo First Python seen: %FIRST_PY%
    echo Install with: python -m pip install streamlit anastruct numpy matplotlib
    echo Or use the portable BridgeStudio-App package instead.
    pause
    exit /b 1
)

REM ---- Solver location for a same-layout checkout -----------------
if not defined FEM_EIGEN_DIR (
    if exist "%~dp0..\FEM-Eigen\build" set "FEM_EIGEN_DIR=%~dp0..\FEM-Eigen"
)

REM ---- Kill stale process holding the port ------------------------
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
echo Python: %PY%
echo URL: http://localhost:%PORT%
echo (browser opens automatically in a few seconds)

start "" /b cmd /c "timeout /t 4 >nul & start "" http://localhost:%PORT%"

"%PY%" -m streamlit run app.py --server.port %PORT% --server.headless true
pause
exit /b 0

REM ---------------------------------------------------------------
:try_py
REM %~1 = candidate python.exe path (quoted by caller)
if not exist "%~1" exit /b 0
if not defined FIRST_PY set "FIRST_PY=%~1"
"%~1" -c "import streamlit, anastruct, numpy, matplotlib" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
