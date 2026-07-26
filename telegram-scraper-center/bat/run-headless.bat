@echo off
REM ============================================================================
REM  Menjalankan campaign tanpa dashboard (cocok untuk Task Scheduler / VPS).
REM  Progres tetap tercatat di database dan dikirim lewat bot notifikasi.
REM  Ctrl+C menghentikan dengan aman - antrean tersimpan dan bisa dilanjutkan.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat
if not exist "logs" mkdir logs

REM Nama berkas log: logs\run_YYYYMMDD_HHMMSS.log
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%t
if "%STAMP%"=="" set STAMP=manual

echo.
echo   Menjalankan campaign. Log tersimpan di logs\run_%STAMP%.log
echo   Tekan Ctrl+C untuk berhenti dengan aman.
echo.

REM Tee-Object menampilkan ke layar sekaligus menulis ke berkas log.
powershell -NoProfile -Command ^
  "python -m tsc run 2>&1 | Tee-Object -FilePath 'logs\run_%STAMP%.log'; exit $LASTEXITCODE"
set RC=%ERRORLEVEL%

echo.
echo   Selesai dengan kode %RC%.
pause
exit /b %RC%
