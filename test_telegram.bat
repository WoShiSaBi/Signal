@echo off
cd /d "%~dp0"
echo Sending Telegram test message...
echo.
python test_telegram.py
echo.
pause
