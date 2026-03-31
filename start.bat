@echo off
title Flowrex Algo
echo Starting Flowrex Algo...
echo.

start "Flowrex Backend" cmd /k "cd /d D:\AI\Flowrex Algo\backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 3 /nobreak >nul

start "Flowrex Frontend" cmd /k "cd /d D:\AI\Flowrex Algo\frontend && npm run dev"

echo.
echo Backend: http://localhost:8000
echo Frontend: http://localhost:3000
echo.
echo Both servers starting in separate windows.
timeout /t 3 /nobreak >nul
