@echo off
title Solo Miner Optimizer
cd /d "%~dp0"
where py >nul 2>nul && (py -3 solo_optimizer.py & goto end)
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" ("%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" solo_optimizer.py & goto end)
python solo_optimizer.py
:end
if errorlevel 1 pause
