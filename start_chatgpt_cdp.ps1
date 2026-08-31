# 完全退出 ChatGPT (Codex 桌面版) 后，以 CDP 调试端口重新启动
# 用法: powershell -ExecutionPolicy Bypass -File start_chatgpt_cdp.ps1

param(
    [int]$Port = 9223
)

$ErrorActionPreference = "Stop"

# MSIX 打包的应用必须通过 AppsFolder 启动，直接调 exe 会被沙箱拦截
$appId = "OpenAI.Codex_2p2nqsd0c76g0!App"

$pkg = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue
if (-not $pkg) {
    Write-Host "[错误] 未找到 OpenAI.Codex 包（ChatGPT 桌面版未安装？）" -ForegroundColor Red
    exit 1
}
$appId = "$($pkg.PackageFamilyName)!App"

$running = Get-Process -Name "ChatGPT" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "[提示] 正在关闭 $($running.Count) 个 ChatGPT 进程…" -ForegroundColor Yellow
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

Write-Host "[启动] shell:AppsFolder\$appId --remote-debugging-port=$Port --remote-allow-origins=*"
Start-Process -FilePath "shell:AppsFolder\$appId" -ArgumentList "--remote-debugging-port=$Port", "--remote-allow-origins=*"

$cdpUrl = "http://127.0.0.1:$Port/json"
Write-Host "[等待] CDP 端口就绪 ($cdpUrl) ..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $cdpUrl -TimeoutSec 2 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            Write-Host "[成功] CDP 已就绪: $cdpUrl" -ForegroundColor Green
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

Write-Host "[警告] 30 秒内未检测到 CDP。" -ForegroundColor Yellow
Write-Host "手动验证: 浏览器打开 $cdpUrl"
exit 1
