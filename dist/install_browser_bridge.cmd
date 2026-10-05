@echo off
cd /d "%~dp0"
BrowserBridge.exe --install
if errorlevel 1 echo Bridge installation failed. Keep both EXEs and chrome-extension together.
pause
