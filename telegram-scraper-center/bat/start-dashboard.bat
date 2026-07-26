@echo off
REM ============================================================================
REM  Membuka dashboard Telegram Scraper Center di browser.
REM  Ini pintu masuk utama: semua kontrol campaign ada di halaman tersebut.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

if not exist "config.json" (
    echo   [X] config.json belum ada. Jalankan bat\setup.bat lebih dulu.
    pause
    exit /b 1
)

echo.
echo   Menyalakan dashboard... tutup jendela ini atau tekan Ctrl+C untuk berhenti.
echo.

python -m tsc dashboard
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo   Dashboard berhenti dengan kode %RC%.
    pause
)
exit /b %RC%
