@echo off
REM ============================================================================
REM  Ringkasan kondisi campaign terakhir: progres, status tiap agent, error.
REM  Membaca database saja - aman dijalankan sementara campaign berjalan.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

python -m tsc status
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
