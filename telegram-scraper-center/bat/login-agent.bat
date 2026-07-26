@echo off
REM ============================================================================
REM  Login satu akun agent (mode live).
REM  Telegram akan mengirim kode OTP; ketik di jendela ini.
REM  File sesi tersimpan di folder sessions\ dan dipakai ulang selanjutnya.
REM
REM  Pemakaian:  bat\login-agent.bat admin-1
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

set LABEL=%~1
if "%LABEL%"=="" (
    set /p LABEL="  Label agent sesuai config.json: "
)
if "%LABEL%"=="" (
    echo   [X] Label agent wajib diisi.
    pause
    exit /b 1
)

echo.
echo   Login agent "%LABEL%" ...
echo.

python -m tsc login --agent "%LABEL%"
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
