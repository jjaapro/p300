<#
.SYNOPSIS
    Start the p300 fleet — data feed, every bot runner, the dashboard — each
    in its own console window.

.DESCRIPTION
    One console per process so each one can be watched, stopped (Ctrl+C or
    close the window) and restarted on its own. Consoles stay open after the
    process exits, so a crash traceback is never lost.

    Duplicate guard: a unit that is already running is SKIPPED, never started
    twice (2026-08-15..24 incident: every bot ran doubled for 9 days after a
    manual double-start; chento double-sized its signals). On Windows a
    healthy unit is TWO python.exe processes with the same command line
    (venv shim parent + real interpreter child) — the scan collapses that pair
    the same way dashboard/procscan.py does.

    Run this as the same Windows user that runs the fleet, otherwise the
    process scan cannot see the fleet's command lines.

    Start order: feed first (bots only read what it writes), then the bots,
    then the dashboard. Bots tolerate a feed that is still healing gaps —
    they log stale-input skips until the tables are fresh.

.PARAMETER Units
    Which units to start. Default: all of them (feed, the seven bots, the
    dashboard). Names: feed chento_v3 chento_v3_eth short_squeeze adx carry
    squeeze_bull r4 dashboard monitor. `monitor` is only started when named
    here or via -Monitor.

.PARAMETER Monitor
    Also open a console that runs `python monitor.py` once an hour. This is a
    FALLBACK: monitoring runs as scheduled tasks (\p300\monitor-hourly,
    \p300\monitor-daily-deep, \p300\backup-daily — register them with
    ops\register_tasks.ps1; OPERATIONS.md section 11). While those tasks are
    registered this console only duplicates monitor-hourly, so use it only
    when they are not.

.PARAMETER SkipGapFix
    Pass --skip-gap-fix to feed.py (fast restart, no startup heal). Do NOT use
    after a long outage — the heal pass is what refills LSR/OI before the
    ~30d upstream retention burns them.

.PARAMETER ForceFeed
    Pass --force-start to feed.py. Only needed when the feed died less than
    two minutes ago and its own stale-heartbeat guard refuses to start
    (exit code 3 in the feed console). The process scan in this script still
    refuses to start a second feed regardless of this switch.

.PARAMETER Status
    Only print what is running and exit.

.PARAMETER DryRun
    Print the commands that would be launched and exit.

.EXAMPLE
    .\start_fleet.ps1                      # everything that isn't already up
    .\start_fleet.ps1 -Monitor             # plus the fallback hourly monitor console
    .\start_fleet.ps1 -Units adx,carry     # just two bots
    .\start_fleet.ps1 -Status              # what's running right now

    If PowerShell refuses to run the script:
      powershell -ExecutionPolicy Bypass -File .\start_fleet.ps1
#>
[CmdletBinding()]
param(
    # r4 was held out of the defaults 2026-09-09 (a calendar anomaly with no
    # mechanism) and rejoined them 2026-09-12 with ONLY its ETH windows enabled
    # (bots/r4/config.py ENABLED; reasoning in docs/calibration/r4.md).
    [string[]]$Units = @("feed", "chento_v3", "chento_v3_eth", "short_squeeze",
                         "adx", "carry", "squeeze_bull", "r4", "dashboard"),
    [switch]$Monitor,
    [switch]$SkipGapFix,
    [switch]$ForceFeed,
    [switch]$Status,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Repo   = $PSScriptRoot
$Python = "venv\Scripts\python.exe"        # relative: every console starts in $Repo

if (-not (Test-Path (Join-Path $Repo $Python))) {
    throw "venv interpreter not found at $Repo\$Python"
}

# ---------------------------------------------------------------------------
# The fleet. Edit here to add a bot or change its flags.
#   Script — the argv token that identifies the unit in a process scan
#            (same strings as dashboard/procscan.py UNIT_SCRIPTS).
#   Args   — extra command-line flags.
# ---------------------------------------------------------------------------
$feedArgs = @()
if ($SkipGapFix) { $feedArgs += "--skip-gap-fix" }
if ($ForceFeed)  { $feedArgs += "--force-start" }

$Fleet = [ordered]@{
    feed          = @{ Script = "feed.py";                      Args = $feedArgs }
    chento_v3     = @{ Script = "bots/chento_v3/runner.py";     Args = @() }
    chento_v3_eth = @{ Script = "bots/chento_v3_eth/runner.py"; Args = @() }
    short_squeeze = @{ Script = "bots/short_squeeze/runner.py"; Args = @() }
    adx           = @{ Script = "bots/adx/runner.py";           Args = @() }
    carry         = @{ Script = "bots/carry/runner.py";         Args = @() }
    r4            = @{ Script = "bots/r4/runner.py";            Args = @() }
    squeeze_bull  = @{ Script = "bots/squeeze_bull/runner.py"; Args = @() }
    dashboard     = @{ Script = "dashboard/server.py";          Args = @() }
    monitor       = @{ Script = "monitor.py";                   Args = @(); Hourly = $true }
}

# ---------------------------------------------------------------------------
# Process scan (ground truth; heartbeats cannot count instances).
# ---------------------------------------------------------------------------
function Get-FleetScan {
    $procs = @(Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(python|pwsh|powershell)' -and $_.CommandLine })

    $scan = @{}
    foreach ($unit in $Fleet.Keys) {
        $script = $Fleet[$unit].Script
        # python units are python.exe processes; the hourly loop is a
        # PowerShell host (its python child is transient). Never cross-match:
        # a shell whose command line merely mentions a script is not a bot.
        $exe = if ($Fleet[$unit].Hourly) { '^(pwsh|powershell)' } else { '^python' }
        # match the script token, not the interpreter path (the venv python
        # also runs editor language servers)
        $pattern = '(^|[\s/"''])' + [regex]::Escape($script) + '([\s"'']|$)'
        $matched = @($procs | Where-Object {
            $_.Name -match $exe -and
            $_.CommandLine.ToLower().Replace("\", "/") -match $pattern
        })
        $parents = @($matched | ForEach-Object { $_.ParentProcessId })
        # an instance = a leaf of the parent/child graph (venv shim collapse)
        $leaves = @($matched | Where-Object { $parents -notcontains $_.ProcessId })
        $scan[$unit] = @{
            Instances = $leaves.Count
            Pids      = @($leaves | ForEach-Object { $_.ProcessId })
        }
    }
    # legacy monolith: embeds its own feed thread -> double-fetching
    $legacy = '(^|[\s/"''])bot\.py([\s"'']|$)'
    $scan["_legacy_bot_py"] = @($procs | Where-Object {
        $_.CommandLine.ToLower().Replace("\", "/") -match $legacy
    } | ForEach-Object { $_.ProcessId })
    return $scan
}

function Show-FleetScan($scan) {
    foreach ($unit in $Fleet.Keys) {
        $s = $scan[$unit]
        $state = switch ($s.Instances) {
            0       { "down" }
            1       { "running   pid " + $s.Pids[0] }
            default { "DUPLICATE x$($s.Instances)  pids " + ($s.Pids -join ", ") }
        }
        "{0,-15} {1}" -f $unit, $state
    }
    if ($scan["_legacy_bot_py"].Count -gt 0) {
        Write-Warning ("legacy bot.py is running (pids " +
            ($scan["_legacy_bot_py"] -join ", ") +
            ") - it double-fetches against feed.py. Stop it.")
    }
}

# ---------------------------------------------------------------------------
# Launch one unit in a fresh console that survives the process exiting.
# ---------------------------------------------------------------------------
function Start-UnitConsole([string]$unit) {
    $u     = $Fleet[$unit]
    $title = "p300 $unit"

    if ($u.Hourly) {
        # PowerShell loop: run once an hour, console stays open (-NoExit).
        $loop = "`$host.UI.RawUI.WindowTitle = '$title'; " +
                "while (`$true) { & '$Python' $($u.Script) $($u.Args -join ' '); " +
                "Write-Host ('--- next run ' + (Get-Date).AddHours(1).ToString('HH:mm')); " +
                "Start-Sleep -Seconds 3600 }"
        $file = (Get-Process -Id $PID).Path         # same PowerShell as this script
        $argline = "-NoExit -ExecutionPolicy Bypass -Command `"$loop`""
    }
    else {
        $cmd = "$Python $($u.Script) $($u.Args -join ' ')".TrimEnd()
        # cmd /k keeps the window open after python exits; /v:on gives the
        # delayed !ERRORLEVEL! so the exit code shown is the real one.
        $inner = "title $title && echo [$title] $cmd && $cmd & " +
                 "echo. & echo [$title] exited with code !ERRORLEVEL! at !DATE! !TIME! " +
                 "-- console left open, close it or re-run start_fleet.ps1"
        $file = $env:ComSpec
        $argline = "/v:on /k `"$inner`""
    }

    if ($DryRun) {
        "  [dry-run] $file $argline"
        return
    }
    Start-Process -FilePath $file -ArgumentList $argline -WorkingDirectory $Repo
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
Set-Location $Repo
$scan = Get-FleetScan

if ($Status) {
    Show-FleetScan $scan
    exit 0
}

$wanted = @($Units | ForEach-Object { $_.ToLower() })
if ($Monitor -and $wanted -notcontains "monitor") { $wanted += "monitor" }
$unknown = @($wanted | Where-Object { -not $Fleet.Contains($_) })
if ($unknown.Count -gt 0) {
    throw "unknown unit(s): $($unknown -join ', '). Known: $($Fleet.Keys -join ', ')"
}

"p300 fleet launcher - $Repo"
"before:"
Show-FleetScan $scan
""

if ($wanted -contains "feed" -and $scan["_legacy_bot_py"].Count -gt 0) {
    Write-Warning "not starting feed while legacy bot.py runs (it would double-fetch)."
    $wanted = @($wanted | Where-Object { $_ -ne "feed" })
}

$started = @()
foreach ($unit in $Fleet.Keys) {              # $Fleet order = start order
    if ($wanted -notcontains $unit) { continue }
    if ($scan[$unit].Instances -gt 0) {
        "skip   $unit  (already running, pid $($scan[$unit].Pids -join ', '))"
        continue
    }
    "start  $unit"
    Start-UnitConsole $unit
    $started += $unit
    if (-not $DryRun) { Start-Sleep -Seconds 2 }   # stagger DB/WAL init
}

if ($DryRun -or $started.Count -eq 0) {
    ""
    if ($started.Count -eq 0 -and -not $DryRun) { "nothing to start." }
    exit 0
}

Start-Sleep -Seconds 5
""
"after:"
Show-FleetScan (Get-FleetScan)
""
"dashboard: http://127.0.0.1:8300   (fleet tiles show DUPLICATE/DEAD/SILENT)"
"check:     python monitor.py       (freshness + heartbeats; feed heals gaps first)"
