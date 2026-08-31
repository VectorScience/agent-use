@echo off
rem 完全退出 ChatGPT 后以 CDP 调试端口 (9223) 重启
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_chatgpt_cdp.ps1" %*
