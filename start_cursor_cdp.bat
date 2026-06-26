@echo off
REM Start Cursor with the CDP debugging port enabled.
REM Existing Cursor windows are preserved when possible.

setlocal
set CURSOR_EXE=%LOCALAPPDATA%\Programs\cursor\Cursor.exe

if not exist "%CURSOR_EXE%" (
  echo Cursor executable not found at:
  echo   %CURSOR_EXE%
  echo Edit this script if Cursor is installed elsewhere.
  exit /b 1
)

echo Starting Cursor with --remote-debugging-port=9222 ...
start "" "%CURSOR_EXE%" --remote-debugging-port=9222
endlocal
