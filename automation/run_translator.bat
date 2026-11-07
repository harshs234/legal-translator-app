@echo off
REM ====================================================================
REM  Mutarjim Translator Service — starts the FastAPI pipeline on :8000
REM  This is the engine n8n calls.  It also auto-creates the inbox/
REM  processed/ output/ folders on first start.
REM ====================================================================

REM --- Settings you may want to change ---------------------------------
set VENV_NAME=venv313
set PORT=8000
REM AUTO_APPROVE_LOW_CONFIDENCE=1  -> never stop for a human on uncertain
REM docs (translate with best-guess class). Set to 0 to park them instead.
set AUTO_APPROVE_LOW_CONFIDENCE=1
REM --------------------------------------------------------------------

REM Jump to the project root (one level up from this automation\ folder)
cd /d "%~dp0.."

REM Activate the virtual environment
call "%VENV_NAME%\Scripts\activate.bat"

REM Start the service (LLM key is read from your .env via config.py)
python -m uvicorn translator_service:app --host 0.0.0.0 --port %PORT%
