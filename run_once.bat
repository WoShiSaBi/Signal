@echo off
cd /d "%~dp0"
echo Running one test scan in the current config mode...
echo.
python main.py --once
echo.
echo Finished one scan. Check logs\bot.log for details.
pause
