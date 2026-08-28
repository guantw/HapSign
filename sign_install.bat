@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

REM Python environment (conda or venv)
if defined HAPSIGN_PYTHON (
    set PYTHON=%HAPSIGN_PYTHON%
) else (
    set PYTHON=python
)

REM Cache control (set HAPSIGN_REFRESH_TOKEN=1 or HAPSIGN_REFRESH_SIGNING=1)
set EXTRA_ARGS=
if "%HAPSIGN_REFRESH_TOKEN%"=="1" set EXTRA_ARGS=%EXTRA_ARGS% --refresh-token
if "%HAPSIGN_REFRESH_SIGNING%"=="1" set EXTRA_ARGS=%EXTRA_ARGS% --refresh-signing
if defined HAPSIGN_EXTRA_ARGS set EXTRA_ARGS=%EXTRA_ARGS% %HAPSIGN_EXTRA_ARGS%

if "%~1"=="" (
    echo Usage: drag a .hap file onto this script, or:
    echo   sign_install.bat path\to\app-unsigned.hap device-serial
    echo.
    echo Environment variables:
    echo   HAPSIGN_PYTHON             Python path
    echo   HAPSIGN_SERIAL             Required HDC target serial
    echo   HAPSIGN_REFRESH_TOKEN=1    Force refresh token cache
    echo   HAPSIGN_REFRESH_SIGNING=1  Force refresh signing files
    echo   HAPSIGN_EXTRA_ARGS         Extra CLI args ^(e.g. --enable-capability^)
    pause
    exit /b 1
)

set TARGET_SERIAL=%HAPSIGN_SERIAL%
if not "%~2"=="" set TARGET_SERIAL=%~2
if not defined TARGET_SERIAL (
    echo Error: device serial is required.
    echo Run the following command, then pass a Connected USB serial as argument 2
    echo or set HAPSIGN_SERIAL:
    echo.
    "%PYTHON%" -u main.py devices list
    echo.
    pause
    exit /b 2
)

echo ========================================
echo   hapsign - Auto Sign ^& Install
echo ========================================
echo   Hap: %~1
echo   Serial: %TARGET_SERIAL%
if defined EXTRA_ARGS echo   Extra: %EXTRA_ARGS%
echo.

"%PYTHON%" -u main.py deploy --hap "%~1" --serial "%TARGET_SERIAL%" %EXTRA_ARGS%

echo.
if %ERRORLEVEL% equ 0 (
    echo === Success ===
) else (
    echo === Failed ^(exit code %ERRORLEVEL%^) ===
)
echo.
pause
