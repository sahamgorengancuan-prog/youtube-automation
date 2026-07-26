@echo off
REM ============================================================================
REM  Telegram Scraper Center - Setup awal
REM  Membuat virtualenv, memasang dependency, dan menyiapkan config + .env.
REM  Cukup dijalankan sekali (atau setelah requirements.txt berubah).
REM ============================================================================
setlocal
cd /d "%~dp0.."

echo.
echo   === Telegram Scraper Center - Setup ===
echo.

REM --- Cek Python -------------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo   [X] Python tidak ditemukan di PATH.
    echo       Install Python 3.10+ dari https://python.org lalu centang
    echo       "Add Python to PATH" saat instalasi.
    goto :fail
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Python terdeteksi: %PYVER%

REM --- Virtualenv -------------------------------------------------------------
if not exist ".venv" (
    echo   Membuat virtualenv .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo   [X] Gagal membuat virtualenv.
        goto :fail
    )
) else (
    echo   Virtualenv .venv sudah ada.
)

call .venv\Scripts\activate.bat

REM --- Dependency -------------------------------------------------------------
echo   Memasang dependency ...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [!] Sebagian dependency gagal dipasang.
    echo       Framework tetap bisa jalan di mode simulate ^(hanya butuh Python^).
)

REM --- Berkas konfigurasi -----------------------------------------------------
if not exist "config.json" (
    copy /y "config.example.json" "config.json" >nul
    echo   config.json dibuat dari contoh - silakan sesuaikan.
) else (
    echo   config.json sudah ada, tidak ditimpa.
)

if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo   .env dibuat dari contoh - isi kredensial di sana.
) else (
    echo   .env sudah ada, tidak ditimpa.
)

if not exist "data"     mkdir data
if not exist "sessions" mkdir sessions

echo.
echo   === Setup selesai ===
echo.
echo   Langkah berikutnya:
echo     1. Edit config.json  ^(grup target, grup source, daftar agent^)
echo     2. Edit .env         ^(TSC_API_ID, TSC_API_HASH, TSC_BOT_TOKEN^)
echo     3. Jalankan bat\doctor.bat untuk memeriksa kesiapan
echo     4. Jalankan bat\start-dashboard.bat
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
