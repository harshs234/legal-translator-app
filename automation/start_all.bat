@echo off
REM ====================================================================
REM  start_all.bat — launch both pieces in their own windows, for
REM  manual testing before you set up the auto-start tasks.
REM ====================================================================
start "Mutarjim Translator" cmd /k "%~dp0run_translator.bat"
timeout /t 3 >nul
start "n8n" cmd /k "%~dp0run_n8n.bat"
echo.
echo Translator  -> http://localhost:8000/health
echo n8n editor  -> http://localhost:5678
echo Close the two windows to stop them.
