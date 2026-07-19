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
    echo   sign_install.bat path\to\app-unsigned.hap
    echo.
    echo Environment variables:
    echo   HAPSIGN_PYTHON             Python path
    echo   HAPSIGN_REFRESH_TOKEN=1    Force refresh token cache
    echo   HAPSIGN_REFRESH_SIGNING=1  Force refresh signing files
    echo   HAPSIGN_EXTRA_ARGS         Extra CLI args ^(e.g. --enable-capability^)
    pause
    exit /b 1
)

echo ========================================
echo   hapsign - Auto Sign ^& Install
echo ========================================
echo   Hap: %~1
if defined EXTRA_ARGS echo   Extra: %EXTRA_ARGS%
echo.

"%PYTHON%" -u main.py --hap "%~1" %EXTRA_ARGS%

echo.
if %ERRORLEVEL% equ 0 (
    echo === Success ===
) else (
    echo === Failed ^(exit code %ERRORLEVEL%^) ===
)
echo.
pause
