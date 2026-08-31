# 注册一个 Windows 计划任务，到点自动启动 Cursor（带 CDP 端口）并发送文案。
# 用法示例:
#   .\schedule_task.ps1 -TaskName "PaperHub-0630" -At "06:30" `
#       -WindowTitle "PaperHub" `
#       -Message "按照参考文档，现在开始构建项目..."
#
# 之后在「任务计划程序」(taskschd.msc) 里能看到该任务，可用以下命令管理:
#   schtasks /Query /TN PaperHub-0630
#   schtasks /Run /TN PaperHub-0630      # 立即触发一次（用于测试）
#   schtasks /Delete /TN PaperHub-0630 /F

param(
    [Parameter(Mandatory)][string]$TaskName,
    [Parameter(Mandatory)][string]$At,            # "06:30" 或 "2026-06-30 06:30:00"
    [Parameter(Mandatory)][string]$Message,
    [string]$WindowTitle = $null,
    [int]$CdpPort = 9222
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$clickSend = Join-Path $projectRoot "click_send.py"

# 用打包好的 uv 启动 Cursor + 跑脚本
$wrapped = @(
    "cd `"$projectRoot`"",
    "powershell -NoProfile -ExecutionPolicy Bypass -File `"$projectRoot\start_cursor_cdp.ps1`" -Port $CdpPort",
    "uv run python `"$clickSend`" --send-once --cdp-port $CdpPort"
)
if ($WindowTitle) { $wrapped += "--window-title `"$WindowTitle`"" }
$wrapped += "--message `"$($Message -replace '"', '\"')`""
$cmdLine = ($wrapped -join " && ")

# 时间解析: HH:MM 视为今天的该时刻，已过则报错
$target = $null
if ($At -match "^\d{1,2}:\d{2}$") {
    $today = (Get-Date)
    $target = ([datetime]::Today).Add(([TimeSpan]::Parse($At)))
    if ($target -le $now) {
        Write-Host "[注意] $At 已过，仍会注册（如需明天请用完整日期 2026-06-30 06:30:00）"
    }
} else {
    $target = [datetime]::Parse($At)
}

$startBoundary = $target.ToString("yyyy-MM-ddTHH:mm:ss")

# 先删除同名旧任务
schtasks /Delete /TN $TaskName /F 2>$null | Out-Null

schtasks /Create /TN $TaskName /TR $cmdLine /SC ONCE /ST $target.ToString("HH:mm:ss") /SD $target.ToString("MM/dd/yyyy") /F | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] schtasks 注册失败 (exit=$LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[成功] 已注册计划任务: $TaskName" -ForegroundColor Green
Write-Host "  触发时间: $startBoundary"
if ($WindowTitle) { Write-Host "  目标窗口: $WindowTitle" }
Write-Host "  文案预览: $($Message.Substring(0, [Math]::Min(60, $Message.Length)))..."
Write-Host ""
Write-Host "立即测试:  schtasks /Run /TN $TaskName"
Write-Host "查看状态:  schtasks /Query /TN $TaskName /V /FO LIST"
Write-Host "删除任务:  schtasks /Delete /TN $TaskName /F"
