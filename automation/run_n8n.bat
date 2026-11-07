@echo off
REM ====================================================================
REM  n8n — the scheduler that drives the translator on a timer.
REM  Open http://localhost:5678 after this starts, and make sure your
REM  workflow is toggled ACTIVE (not just saved).
REM ====================================================================

set N8N_PORT=5678

REM If you installed n8n globally (npm i -g n8n) this works as-is.
REM If you use npx instead, replace the line below with:  npx n8n start
n8n start
