param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskAppRoot = Split-Path -Parent $PSScriptRoot
$taskWorkDir = Join-Path $taskAppRoot '.work\siwc-probe'
$taskPython = Join-Path $taskAppRoot '.venv\Scripts\python.exe'
$taskListenerFile = Join-Path $taskWorkDir 'listener.json'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Manga Blendy Python environment is missing.' }
New-Item -ItemType Directory -Path $taskWorkDir -Force | Out-Null

function Get-HealthyListener {
    if (-not (Test-Path -LiteralPath $taskListenerFile)) { return $null }
    try {
        $taskRecord = Get-Content -LiteralPath $taskListenerFile -Raw | ConvertFrom-Json
        if ($taskRecord.service -ne 'manga-blendy-siwc-probe' -or $taskRecord.version -ne 2) { return $null }
        if ($taskRecord.port -lt 1 -or $taskRecord.port -gt 65535) { return $null }
        $taskProcess = Get-Process -Id $taskRecord.pid -ErrorAction Stop
        $taskStartedAt = ([DateTimeOffset]$taskProcess.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds() / 1000.0
        if ([Math]::Abs($taskStartedAt - [double]$taskRecord.created) -gt 2) { return $null }
        $taskLocalUrl = 'http://127.0.0.1:' + $taskRecord.port + '/'
        if ($taskRecord.url -ne $taskLocalUrl) { return $null }
        $taskHealth = Invoke-RestMethod -Uri ($taskLocalUrl + 'health') -TimeoutSec 2
        if ($taskHealth.service -eq 'manga-blendy-siwc-probe' -and $taskHealth.version -eq 2 -and $taskHealth.pid -eq $taskRecord.pid) {
            return $taskLocalUrl
        }
    } catch { return $null }
    return $null
}

$taskUrl = Get-HealthyListener
if (-not $taskUrl) {
    $taskStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
    $env:PYTHONUTF8 = '1'
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('scripts/siwc_probe.py', '--no-browser') -WorkingDirectory $taskAppRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskWorkDir ('listener-' + $taskStamp + '.out.log')) -RedirectStandardError (Join-Path $taskWorkDir ('listener-' + $taskStamp + '.err.log'))
    for ($taskAttempt = 0; $taskAttempt -lt 80; $taskAttempt++) {
        Start-Sleep -Milliseconds 250
        $taskUrl = Get-HealthyListener
        if ($taskUrl) { break }
        $taskProcess.Refresh()
        if ($taskProcess.HasExited) {
            # A concurrent launcher may own the live listener; query it once more.
            $taskUrl = Get-HealthyListener
            break
        }
    }
    if (-not $taskUrl) { throw 'The local sign-in listener is not ready. Run this launcher again after a few seconds.' }
}
Write-Host ('Manga Blendy sign-in listener ready: ' + $taskUrl)
if (-not $NoBrowser) {
    # The person performs authentication and consent in their normal browser.
    Start-Process -FilePath $taskUrl
}
