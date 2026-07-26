@echo off
REM ============================================================================
REM  Memeriksa kesiapan lingkungan: config, kredensial, sesi agent, bot, LLM.
REM  Jalankan ini setiap kali ada yang aneh sebelum melapor bug.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

python -m tsc doctor
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
