@echo off
REM ============================================================================
REM  Menjalankan test otomatis (batching, governor, guard, store, end-to-end).
REM  Seluruh test memakai klien simulasi - tidak menyentuh Telegram sama sekali.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

python -m pytest tests -q
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
