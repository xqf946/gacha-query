@echo off
rem Start the Genshin wish log viewer. Keep this file ASCII-only so it works on any Windows code page.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m wishlog
    goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python -m wishlog
    goto :done
)

echo Python was not found.
echo Please install Python 3 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then run this file again.
:done
pause
