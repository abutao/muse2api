@echo off
chcp 65001 >nul
cd /d C:\Users\fahai\muse-proxy
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8090 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
timeout /t 1 /nobreak >nul
start "muse-proxy" /MIN ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8090
timeout /t 3 /nobreak >nul
netstat -ano | findstr :8090 | findstr LISTENING
