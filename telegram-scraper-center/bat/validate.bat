@echo off
REM ============================================================================
REM  Validasi tanpa mengundang siapa pun:
REM    - tiap agent benar-benar admin di grup TARGET dan boleh menambah member
REM    - tiap grup SOURCE punya minimal satu agent yang berstatus admin
REM  Aman dijalankan kapan saja; tidak ada invite yang dikirim.
REM ============================================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat

python -m tsc validate
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
