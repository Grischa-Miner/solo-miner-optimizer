@echo off
REM Builds dist\SoloMinerOptimizer\SoloMinerOptimizer.exe (one-folder build: fewer antivirus false positives than --onefile)
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PY set "PY=python"
%PY% -m pip install --upgrade pyinstaller || goto err
%PY% -m PyInstaller --noconfirm --onedir --console --noupx --name SoloMinerOptimizer --add-data "index.html;." solo_optimizer.py || goto err
echo.
echo Done: dist\SoloMinerOptimizer\SoloMinerOptimizer.exe
pause
exit /b 0
:err
echo Build failed. Is Python 3.9+ installed? https://www.python.org/downloads/
pause
exit /b 1
