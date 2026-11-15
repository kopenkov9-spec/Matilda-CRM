@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m crm.tls_setup
pause
