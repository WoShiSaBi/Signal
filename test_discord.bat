@echo off
cd /d "%~dp0"
echo Sending Discord webhook test message...
echo.
python test_discord.py
echo.
pause
