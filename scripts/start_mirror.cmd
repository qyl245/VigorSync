@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0mirror-control.ps1" -Action start
if errorlevel 1 pause
