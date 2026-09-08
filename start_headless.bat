@echo off
cd /d "%~dp0"
echo Usage: start_headless.bat --process chrome.exe [--interval 1]
python -m watchdog_perf monitor --headless %*
pause
