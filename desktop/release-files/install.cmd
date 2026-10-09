@echo off
rem Install WildResearchWorkbench for the current user. No administrator rights are needed.
rem Close the running app first; the installer will not replace a running copy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
