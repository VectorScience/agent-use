@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 uv
  exit /b 1
)

uv sync --quiet

echo.
echo Cursor / ChatGPT 定时任务 GUI
echo 浏览器打开: http://127.0.0.1:8765
echo 前提: Cursor 已用 start_cursor_cdp.bat 启动 (端口 9222)
echo       ChatGPT 已用 start_chatgpt_cdp.bat 启动 (端口 9223)
echo 按 Ctrl+C 停止
echo.

uv run python -m scheduler.app %*
