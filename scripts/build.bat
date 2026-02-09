@echo off
REM Build script for Crasher Bot on Windows
REM Usage: scripts\build.bat

cd /d "%~dp0\.."

echo Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo Building executable...
pyinstaller scripts\crasher_bot.spec --distpath dist\ --workpath build\

echo.
echo Build complete!
echo Executable: dist\CrasherBot.exe
pause
