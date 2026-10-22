@echo off
REM Double-click to start applying. Add --dry-run to only list jobs, e.g.  run.bat --dry-run
cd /d "%~dp0"
python main.py %*
pause
