<#
.SYNOPSIS
    Register (or remove) the p300 scheduled tasks: hourly monitor, daily deep
    gap scan, daily prod.db backup.

.DESCRIPTION
    Three Task Scheduler tasks in the \p300\ folder. Each runs
    venv\Scripts\pythonw.exe (no console window) with the repo as its working
    directory, as the current user, and ONLY WHILE THAT USER IS LOGGED ON
    (LogonType Interactive: no stored password, no elevation, RunLevel Limited):

      monitor-hourly      every hour at :07   monitor.py --quiet                        limit  10 min
      monitor-daily-deep  daily 09:10 local   monitor.py --deep --quiet                 limit  20 min
      backup-daily        daily 04:40 local   backup.py --keep-daily 2 --keep-weekly 0  limit 120 min

    Every task may start, and keep running, on battery; runs once to catch up
    after a missed start (StartWhenAvailable); and never overlaps itself
    (MultipleInstances IgnoreNew). Daily start times are stored as LOCAL time,
    so they follow daylight-saving changes.

    pythonw.exe throws stdout away, so the record of every run is in
    data\diagnostics\: monitor_last.json, monitor_last_deep.json,
    backup_last.json, and the rotating monitor.log / backup.log. Alerts go to
    the dashboard only; it reads those files and turns red when the hourly
    monitor stops recording.

    LastTaskResult (Get-ScheduledTaskInfo):
      0       monitor green / backup verified
      1       monitor alerts / backup snapshot, check or prune failed
      2       monitor crashed / backup source missing or not enough disk
      267009  running          267011  has not run yet

    Re-runnable: registering again replaces the three tasks. Other tasks in
    \p300\ are never touched. Do not also run `start_fleet.ps1 -Monitor`: that
    console repeats monitor-hourly.

.PARAMETER Uninstall
    Remove the three tasks, then the \p300\ folder if it is empty. The status
    files, logs and backups under data\ are kept.

.PARAMETER DryRun
    Print what would be registered (or removed) and change nothing.

.EXAMPLE
    pwsh -File ops\register_tasks.ps1 -DryRun      # show the plan
    pwsh -File ops\register_tasks.ps1              # register and verify
    pwsh -File ops\register_tasks.ps1 -Uninstall   # remove
#>
#Requires -Version 7
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Repo     = Split-Path -Parent $PSScriptRoot
$Pythonw  = Join-Path $Repo "venv\Scripts\pythonw.exe"
$TaskPath = "\p300\"
$User     = "$env:USERDOMAIN\$env:USERNAME"

$Tasks = @(
    [pscustomobject]@{ Name = "monitor-hourly"; Script = "monitor.py"
                       Args = "--quiet"; At = "hourly"; LimitMin = 10
                       About = "hourly checks; exit 0 green, 1 alerts, 2 crashed" }
    [pscustomobject]@{ Name = "monitor-daily-deep"; Script = "monitor.py"
                       Args = "--deep --quiet"; At = "09:10"; LimitMin = 20
                       About = "daily interior-gap scan; exit 0 green, 1 alerts, 2 crashed" }
    [pscustomobject]@{ Name = "backup-daily"; Script = "backup.py"
                       Args = "--keep-daily 2 --keep-weekly 0"; At = "04:40"; LimitMin = 120
                       About = "prod.db snapshot, 2 local copies; exit 0 ok, 1 failed, 2 precondition" }
)

function New-P300Trigger($task) {
    if ($task.At -eq "hourly") {
        # -Once plus a 1h repetition with no duration repeats indefinitely.
        $trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Today.AddMinutes(7)) `
            -RepetitionInterval (New-TimeSpan -Hours 1)
    }
    else {
        $trigger = New-ScheduledTaskTrigger -Daily -At $task.At
    }
    # The cmdlet stores the start as UTC ("...Z"), which pins a daily run to
    # UTC and moves it an hour at each DST change. Without an offset Task
    # Scheduler reads it as local time. "s" is culture-invariant
    # (yyyy-MM-ddTHH:mm:ss); a custom "HH:mm" uses this machine's "." separator.
    $trigger.StartBoundary = (Get-Date $trigger.StartBoundary).ToString("s")
    return $trigger
}

function New-P300Settings([int]$minutes) {
    $s = @{
        AllowStartIfOnBatteries    = $true
        DontStopIfGoingOnBatteries = $true
        StartWhenAvailable         = $true
        MultipleInstances          = "IgnoreNew"
        ExecutionTimeLimit         = (New-TimeSpan -Minutes $minutes)
    }
    return New-ScheduledTaskSettingsSet @s
}

function Get-P300Row($name, $action, $trigger, $principal, $settings, $state, $info) {
    [pscustomobject]@{
        Task       = "$TaskPath$name"
        State      = $state
        Logon      = $principal.LogonType
        RunLevel   = $principal.RunLevel
        OnBattery  = (-not $settings.DisallowStartIfOnBatteries) -and (-not $settings.StopIfGoingOnBatteries)
        CatchUp    = $settings.StartWhenAvailable
        Overlap    = $settings.MultipleInstances
        Limit      = $settings.ExecutionTimeLimit
        Start      = $trigger.StartBoundary
        Every      = if ($trigger.Repetition.Interval) { $trigger.Repetition.Interval } else { "P1D" }
        NextRun    = if ($info) { $info.NextRunTime } else { "" }
        LastResult = if ($info) { $info.LastTaskResult } else { "" }
        Command    = "$($action.Execute) $($action.Arguments)  (in $($action.WorkingDirectory))"
    }
}

function Show-P300Tasks {
    $rows = foreach ($t in $Tasks) {
        $st = Get-ScheduledTask -TaskPath $TaskPath -TaskName $t.Name -ErrorAction SilentlyContinue
        if (-not $st) {
            [pscustomobject]@{ Task = "$TaskPath$($t.Name)"; State = "NOT REGISTERED" }
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $t.Name
        Get-P300Row $t.Name $st.Actions[0] $st.Triggers[0] $st.Principal $st.Settings $st.State $info
    }
    $rows | Format-List | Out-String -Width 300
}

# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------
if ($Uninstall) {
    foreach ($t in $Tasks) {
        $st = Get-ScheduledTask -TaskPath $TaskPath -TaskName $t.Name -ErrorAction SilentlyContinue
        if (-not $st) { "absent    $TaskPath$($t.Name)"; continue }
        if ($DryRun) { "[dry-run] would unregister $TaskPath$($t.Name)"; continue }
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $t.Name -Confirm:$false
        "removed   $TaskPath$($t.Name)"
    }
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $folder = $null
    try { $folder = $svc.GetFolder("\p300") } catch { $folder = $null }   # no such folder
    if ($folder -and $folder.GetTasks(1).Count -eq 0 -and $folder.GetFolders(0).Count -eq 0) {
        if ($DryRun) { "[dry-run] would remove the empty folder \p300\" }
        else { $svc.GetFolder("\").DeleteFolder("p300", 0); "removed   folder \p300\" }
    }
    elseif ($folder) {
        "kept      folder \p300\ (it still holds other tasks)"
    }
    ""
    "The dashboard will show MONITOR_STALE / MONITOR_NEVER_RUN from now on - the honest state."
    exit 0
}

# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------
foreach ($f in @($Pythonw, (Join-Path $Repo "monitor.py"), (Join-Path $Repo "backup.py"))) {
    if (-not (Test-Path $f)) { throw "not found: $f" }
}

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$planned = @()
foreach ($t in $Tasks) {
    $action   = New-ScheduledTaskAction -Execute $Pythonw -Argument "$($t.Script) $($t.Args)" `
                    -WorkingDirectory $Repo
    $trigger  = New-P300Trigger $t
    $settings = New-P300Settings $t.LimitMin
    if ($DryRun) {
        $planned += Get-P300Row $t.Name $action $trigger $principal $settings "(dry-run: not registered)" $null
        continue
    }
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $t.Name -Action $action `
        -Trigger $trigger -Principal $principal -Settings $settings `
        -Description "p300 $($t.Script) $($t.Args): $($t.About). See OPERATIONS.md section 11." `
        -Force | Out-Null
    "registered $TaskPath$($t.Name)"
}

if ($DryRun) {
    "p300 scheduled tasks - DRY RUN, nothing registered (user $User, repo $Repo)"
    $planned | Format-List | Out-String -Width 300
    exit 0
}

""
"verification:"
Show-P300Tasks
"Run one now:  Start-ScheduledTask -TaskPath '\p300\' -TaskName 'monitor-hourly'"
"Then:         Get-ScheduledTaskInfo -TaskPath '\p300\' -TaskName 'monitor-hourly' | Format-List LastRunTime, LastTaskResult, NextRunTime"
"Record:       Get-Content $Repo\data\diagnostics\monitor_last.json"
