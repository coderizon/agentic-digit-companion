@echo off
setlocal
cd /d "%~dp0"
py -3 -X utf8 "demo\lokal.py" %*
if errorlevel 1 pause
