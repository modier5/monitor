@echo off
cd /d "%~dp0"
python -m watchdog_perf monitor
pause
