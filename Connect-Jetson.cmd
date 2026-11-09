@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\connect-jetson.ps1"
pause
