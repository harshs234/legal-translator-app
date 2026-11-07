@echo off
REM ====================================================================
REM  Mutarjim Translation DAEMON — the robust, unattended translator.
REM
REM  Unlike the n8n+HTTP approach, this runs the pipeline directly in a
REM  loop: it scans the inbox, then translates each queued job ONE AT A
REM  TIME, fully finishing one before starting the next. No HTTP, so no
REM  5-minute timeouts and no overlapping runs — however long a document
REM  takes, it just works.
REM
REM  Drop files into inbox\to_english | to_arabic | to_french and leave
REM  this running. That's the whole translation system.
REM ====================================================================

set VENV_NAME=venv313
set AUTO_APPROVE_LOW_CONFIDENCE=1

REM --interval 60     = when the queue is empty, re-scan the inbox every 60s
REM --max-per-cycle 20 = drain up to 20 queued jobs back-to-back before sleeping
cd /d "%~dp0.."
call "%VENV_NAME%\Scripts\activate.bat"
python scheduled_agent.py --daemon --interval 60 --max-per-cycle 20
