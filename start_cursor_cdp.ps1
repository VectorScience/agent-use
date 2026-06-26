# 完全退出 Cursor 后，以 CDP 调试端口重新启动
# 用法: powershell -ExecutionPolicy Bypass -File start_cursor_cdp.ps1

param(
    [int]$Port = 9222
)

$ErrorActionPreference = "Stop"
$cursorExe = Join-Path $env:LOCALAPPDATA "Programs\cursor\Cursor.exe"

if (-not (Test-Path $cursorExe)) {
    Write-Host "[错误] 未找到 Cursor: $cursorExe" -ForegroundColor Red
    exit 1
}

$running = Get-Process -Name "Cursor" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "[提示] 正在关闭 $($running.Count) 个 Cursor 进程，请先确认已保存工作..." -ForegroundColor Yellow
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

Write-Host "[启动] $cursorExe --remote-debugging-port=$Port"
Start-Process -FilePath $cursorExe -ArgumentList "--remote-debugging-port=$Port"

$cdpUrl = "http://127.0.0.1:$Port/json"
Write-Host "[等待] CDP 端口就绪 ($cdpUrl) ..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $cdpUrl -TimeoutSec 2 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            Write-Host "[成功] CDP 已就绪。可在浏览器打开: $cdpUrl" -ForegroundColor Green
            Write-Host "然后运行: uv run click_send.py --list-windows"
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

Write-Host "[警告] 30 秒内未检测到 CDP。请查看 Cursor 启动窗口是否出现 DevTools listening 行。" -ForegroundColor Yellow
Write-Host "手动验证: 浏览器打开 $cdpUrl"
exit 1
