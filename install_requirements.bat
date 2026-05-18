@echo off
cd /d "%~dp0"
echo Installing Python requirements...
python -m pip install -r requirements.txt
echo.
echo Done.
pause
