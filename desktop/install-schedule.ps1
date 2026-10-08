<#
Runs the models in the background every hour, 9 AM to 11 PM, so predictions exist
before games start even when the app isn't open (an NHL game no run predicted before
puck drop can't be graded). Each run is the app's "Run what's due": the same rules,
kickoff freeze and checks, no window; if the app is open, it runs there instead.

  .\desktop\install-schedule.ps1            # create or update the task
  .\desktop\install-schedule.ps1 -Remove    # delete it

The PC has to be on (and you signed in): a run missed while it was off or asleep
happens as soon as it's back. See the task in Task Scheduler as "Sports Models".
#>
param(
    [switch]$Remove,
    [int]$FirstHour = 9,   # 9 AM
    [int]$LastHour = 23    # 11 PM
)
$ErrorActionPreference = 'Stop'
$name = 'Sports Models'
$app = Join-Path $PSScriptRoot 'app.py'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the '$name' task." -ForegroundColor Green
    return
}

# The real interpreter (not the Microsoft Store alias), via the py launcher when there is one.
$python = $null
if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $python = (& py.exe -3 -c 'import sys; print(sys.executable)').Trim()
}
if (-not $python) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'  # no console window
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }

$first = (Get-Date).Date.AddHours($FirstHour)
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$app`" --background" `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$trigger = New-ScheduledTaskTrigger -Daily -At $first
# Every hour from the first run through the last (the window is inclusive of its last hour).
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $first -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours ($LastHour - $FirstHour) -Minutes 1)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Runs what's due for the sports models (desktop\app.py --background), every hour $FirstHour`:00-$LastHour`:00." `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $name
Write-Host "Scheduled '$name': every hour, $FirstHour`:00 to $LastHour`:00. Next run: $($info.NextRunTime)" -ForegroundColor Green
