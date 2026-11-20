@echo off
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ". '%~dp0python-env.ps1'; $python = Resolve-VigorSyncPython -ProjectRoot (Get-Location).Path; Start-Process -FilePath $python -ArgumentList 'apps\desktop\app.py' -WindowStyle Hidden"
