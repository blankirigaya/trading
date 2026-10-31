# Supervises the AI Trader backend: keeps uvicorn listening on :8000 and the
# paper-trading session attached. Restarts both when the listener dies.
#
# Why this exists: on this machine the uvicorn accept loop intermittently dies
# with WinError 64 ("network name no longer available") during network flaps.
# The event loop and background tasks survive, but the HTTP API goes dark, so
# the UI shows stale data and controls stop working. The backend therefore runs
# under the selector event loop (ai_trader.serve), which does not use IOCP for
# accept, and this watchdog restarts it if the listener ever disappears anyway.
# Paper books persist in SQLite (paper_* tables), so a restart resumes the same
# positions and cash instead of flattening them.
#
# Stop it with: Get-CimInstance Win32_Process | Where-Object {
#   $_.CommandLine -like "*supervise_backend.ps1*" } | ForEach-Object {
#   Stop-Process -Id $_.ProcessId -Force }

$ErrorActionPreference = "SilentlyContinue"
$Workdir = "D:\trading\ai-trader"
$Log = "C:\Users\ompla\AppData\Local\Temp\opencode\supervisor.log"
$BackendLog = "C:\Users\ompla\AppData\Local\Temp\opencode\backend.log"
$consecutiveQuickDeaths = 0
# PID of the backend tree this watchdog started. Tracked so restarts never
# need a WMI sweep, which hangs on this machine.
$script:backendPid = $null

function Write-Log([string]$msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    $line | Out-File -FilePath $Log -Append -Encoding utf8
    Write-Output $line
}

$LockFile = "C:\\Users\\ompla\\AppData\\Local\\Temp\\opencode\\supervisor.lock"

function Test-OtherSupervisor {
    # CIM process data goes stale on this machine; Get-Process is accurate.
    # The lock file holds the watcher's PID; a PID with no live process (or
    # our own PID) means no rival.
    if (-not (Test-Path $LockFile)) { return $false }
    $lockPid = Get-Content $LockFile -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $lockPid) { return $false }
    $proc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
    return ($null -ne $proc) -and ($proc.Id -ne $PID)
}

function Write-Heartbeat {
    Set-Content -Path $LockFile -Value $PID -NoNewline -ErrorAction SilentlyContinue
}

function Test-Port {
    # TCP connect probe with a hard timeout: tests whether the backend truly
    # accepts connections (better signal than LISTEN state) and can never
    # hang the watchdog the way WMI/NetTCPConnection calls do on this machine.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", 8000, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(3000)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ListenerPid {
    # netstat parsing instead of Get-NetTCPConnection: native, fast, no WMI.
    foreach ($line in (netstat -ano -p TCP 2>$null)) {
        if ($line -match "127\.0\.0\.1:8000\s+.*LISTENING\s+(\d+)") {
            return [int]$Matches[1]
        }
        if ($line -match "0\.0\.0\.0:8000\s+.*LISTENING\s+(\d+)") {
            return [int]$Matches[1]
        }
    }
    return $null
}

function Test-ListenerGone {
    # One failed probe must never murder a healthy backend: only report the
    # listener gone after three consecutive misses a few seconds apart.
    for ($i = 0; $i -lt 3; $i++) {
        if (Test-Port) { return $false }
        if ($i -lt 2) { Start-Sleep -Seconds 5 }
    }
    return $true
}

function Wait-ForPort([int]$timeoutSec) {
    for ($i = 0; $i -lt $timeoutSec; $i += 2) {
        if (Get-ListenerPid) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Stop-Backend {
    # Kill the exact tree we started (tracked PID) plus whatever holds the
    # port now. No CIM/WMI sweep: WMI hangs on this machine and would wedge
    # the watchdog exactly when recovery matters most.
    foreach ($p in @($script:backendPid, (Get-ListenerPid)) | Where-Object { $_ }) {
        taskkill /PID $p /T /F 2>$null | Out-Null
    }
    $script:backendPid = $null
    Start-Sleep -Seconds 3
}

Write-Log "supervisor starting (3-shard paper session, auto re-attach on crash)"

# Singleton: a second watchdog would fight the first (each killing the other's
# just-started backend). Exit if one is already watching.
if (Test-OtherSupervisor) {
    Write-Log "another supervisor is already running (see supervisor.lock); exiting"
    exit 0
}
Write-Heartbeat

while ($true) {
    if (-not (Test-Port)) {
        Stop-Backend
        # Preserve the dead backend's log before the fresh one overwrites it.
        Copy-Item $BackendLog "$BackendLog.prev" -Force -ErrorAction SilentlyContinue
        Write-Log "starting backend"
        $proc = Start-Process -FilePath "cmd.exe" `
            -ArgumentList "/c uv run python -m ai_trader.serve --host 127.0.0.1 --port 8000 > `"$BackendLog`" 2>&1" `
            -WorkingDirectory $Workdir -WindowStyle Minimized -PassThru
        $script:backendPid = $proc.Id

        if (-not (Wait-ForPort 90)) {
            Write-Log "backend failed to bind :8000 within 90s; retrying"
            $consecutiveQuickDeaths += 1
            if ($consecutiveQuickDeaths -ge 3) {
                Write-Log "three consecutive bind failures; giving up (check the code/backend log)"
                break
            }
            continue
        }
    } else {
        # A backend is already serving (e.g. supervisor relaunched after its
        # own exit): adopt it instead of killing live paper books, and take
        # ownership of its PID so a later restart targets the right tree.
        Write-Log "listener already up; adopting without restart"
        $script:backendPid = Get-ListenerPid
    }

    try {
        $health = curl.exe -s --max-time 10 http://127.0.0.1:8000/health
        Write-Log "health: $health"
    } catch { Write-Log "health check failed (continuing anyway)" }

    # Idempotent: attach_runtime is a no-op when a session is already attached.
    try {
        $started = curl.exe -s -X POST --max-time 60 http://127.0.0.1:8000/control/start
        Write-Log "control/start -> $started"
    } catch { Write-Log "control/start failed (FYERS token may be expired; will retry)" }

    # The watch loop below only exits when the listener disappears. How long it
    # served tells a healthy stretch apart from a crash loop: restarts that die
    # within 2 minutes of serving count toward giving up; anything longer
    # resets the counter. (Startup itself takes ~20s, so time-since-launch
    # is deliberately not a signal.)
    $watchStart = Get-Date
    # Watch the port; restart when the backend stops accepting connections.
    # Test-Port needs ~10s of misses before it reports gone.
    while (-not (Test-ListenerGone)) { Start-Sleep -Seconds 15; Write-Heartbeat }
    $servedFor = ((Get-Date) - $watchStart).TotalSeconds
    if ($servedFor -lt 120) {
        $consecutiveQuickDeaths += 1
        if ($consecutiveQuickDeaths -ge 5) {
            Write-Log "listener dying within 2 min repeatedly; giving up (check backend log)"
            break
        }
    } else {
        $consecutiveQuickDeaths = 0
    }
    Write-Log "listener on :8000 gone after $([int]$servedFor)s; restarting backend"
}
