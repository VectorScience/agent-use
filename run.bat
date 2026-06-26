@echo off
REM One-shot dev launcher: start Cursor (CDP) + backend + frontend dev server.

setlocal
cd /d "%~dp0"

echo [1/3] Starting Cursor with CDP ...
call start_cursor_cdp.bat

echo [2/3] Installing backend deps via uv ...
uv sync --quiet

echo [3/3] Starting backend (FastAPI on :8000) ...
start "Cursor Remote API" cmd /c "uv run python -m server.main --port 8000"

echo.
echo Backend:  http://127.0.0.1:8000   (LAN: http://<this-pc-ip>:8000)
echo API docs: http://127.0.0.1:8000/docs
echo.
echo To start the frontend dev server (separate terminal):
echo   cd frontend ^&^& npm install ^&^& npm run dev
echo.
endlocal
