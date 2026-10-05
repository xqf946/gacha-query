@echo off
rem Build GenshinWishLog.exe on Windows. Keep this file ASCII-only so it works on any Windows code page.
rem Needs Python 3 on this PC (only for building; the finished exe runs on PCs without Python).
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (
    where python >nul 2>nul && set PY=python
)
if not defined PY (
    echo Python was not found.
    echo Please install Python 3 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer, then run this file again.
    goto :end
)

echo [1/2] Installing PyInstaller...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto :fail

echo [2/2] Building the exe...
%PY% -m PyInstaller --noconfirm --clean --onefile --name GenshinWishLog --add-data "wishlog\static;wishlog\static" run.py
if errorlevel 1 goto :fail

echo.
echo Done. Your app is: %~dp0dist\GenshinWishLog.exe
echo Copy it to any folder you like and double-click it. Records are saved in a "data" folder next to it.
goto :end

:fail
echo.
echo Build failed. Please send the messages above to the developer.

:end
pause
