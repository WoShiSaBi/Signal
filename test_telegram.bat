@echo off
cd /d "%~dp0"
echo Sending formatted Telegram signal test...
echo.
python test_telegram.py
echo.
pause
