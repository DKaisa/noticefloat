# NoticeFloat 一键启动脚本（开机自启用）
# 会启动：backend (FastAPI) + cpolar (免费版 http 隧道)
# 幂等：如果已经在跑，跳过

$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\zhangkai_b\work\project\AI记录\NoticeFloat'

# ---- 1. 启动 backend ----
$backendRunning = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*NoticeFloat*main.py*' }

if (-not $backendRunning) {
    Push-Location "$root\backend"
    Start-Process -FilePath '.\.venv\Scripts\python.exe' `
        -ArgumentList 'main.py' `
        -RedirectStandardOutput 'out.log' `
        -RedirectStandardError 'err.log' `
        -WindowStyle Hidden
    Pop-Location
    Write-Host "[startup] backend started"
} else {
    Write-Host "[startup] backend already running (pid=$($backendRunning.ProcessId))"
}

# ---- 2. 启动 cpolar ----
$cpolarRunning = Get-CimInstance Win32_Process -Filter "Name = 'cpolar.exe'"

if (-not $cpolarRunning) {
    Start-Process -FilePath "$root\cpolar\cpolar.exe" `
        -ArgumentList 'http', '-region', 'cn', `
                      "-log=$root\cpolar\cpolar.log", `
                      '-log-level=info', '8787' `
        -WindowStyle Hidden
    Write-Host "[startup] cpolar started"
} else {
    Write-Host "[startup] cpolar already running (pid=$($cpolarRunning.ProcessId))"
}

Start-Sleep 5

# ---- 3. 健康检查 ----
try {
    $r = Invoke-RestMethod 'http://127.0.0.1:8787/api/latest_pc' -TimeoutSec 5
    Write-Host "[startup] backend OK, version=$($r.versionName)"
} catch {
    Write-Host "[startup] backend health check FAILED: $($_.Exception.Message)"
}
