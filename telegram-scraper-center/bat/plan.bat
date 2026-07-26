@echo off
REM ============================================================================
REM  Menyusun ulang pembagian batch dan menampilkan rencananya.
REM  Berguna setelah menambah/menghapus agent atau setelah ada agent dilimitasi.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

python -m tsc plan
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
