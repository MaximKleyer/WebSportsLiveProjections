<#
One command to refresh the site's model data ("call both models").

  .\scripts\update-data.ps1                              # re-export the CSVs the models already have
  .\scripts\update-data.ps1 -Week 3                      # same, but open the week picker on week 3
  .\scripts\update-data.ps1 -Predict -Week 3             # run every model (football for week 3), then export
  .\scripts\update-data.ps1 -Sport cfb -Predict -Week 3  # one sport only
  .\scripts\update-data.ps1 -Sport nhl -Predict          # NHL: today's games, season sim, power rankings
  .\scripts\update-data.ps1 -Sport nba -Predict          # NBA: season sim, power rankings (the day's games
                                                         #   are the model's "NBA gameday" task's)
  .\scripts\update-data.ps1 -Grade                       # grade the football models' finished games first
  .\scripts\update-data.ps1 -Grade -Predict -Week 5      # the weekly routine: grade, project week 5, export

-Predict also refreshes each model's season sim (can take a few minutes).
Football needs -Week with -Predict; the NHL predicts the next game day on its own.
-Grade runs each football model's own grader over every saved week (re-grading
a finished week reproduces it; a week in progress counts the games played so
far), before any -Predict. The NHL needs no grading step: its daily run pulls
in results and settles bets, and its adapter grades from them.
Each model command runs through the desktop app's protected step
(desktop\sports\<sport>.py step ...): a game that kicked off before the run keeps
its earlier projection (the kickoff freeze), the rewritten file is backed up
under desktop\.state\backups, and an empty or missing output fails the step.
Each sport's adapter (scripts\exporters\<sport>.py) runs with that model's own
venv Python; then every data file is validated (scripts\site_export.py check).
After it finishes: review the diff, then commit + push this repo to deploy.
The desktop app (desktop\app.py) does all of this on its own, picking the week.
#>
param(
    [ValidateSet('nfl', 'cfb', 'nhl', 'nba', 'all')]
    [string]$Sport = 'all',
    [switch]$Predict,
    [switch]$Grade,
    [int]$Week,
    # January-February games (the NFL playoffs) belong to the season that began the year before.
    [int]$Season = $(if ((Get-Date).Month -le 2) { (Get-Date).Year - 1 } else { (Get-Date).Year })
)

$ErrorActionPreference = 'Stop'
$NflRepo = 'C:\Users\maxim\WebSportsLiveProjections\models\nfl'
$CfbRepo = 'C:\Users\maxim\WebSportsLiveProjections\models\cfb'
$NhlRepo = 'C:\Users\maxim\WebSportsLiveProjections\models\nhl'
$NbaRepo = 'C:\Users\maxim\WebSportsLiveProjections\models\nba'
$Steps = Join-Path (Split-Path $PSScriptRoot -Parent) 'desktop\sports'  # protected model steps

function Invoke-Step {
    param([string]$Label, [string]$Exe, [string[]]$CmdArgs, [string]$Cwd)
    Write-Host ""
    Write-Host "== $Label" -ForegroundColor Cyan
    Push-Location $Cwd
    try {
        & $Exe @CmdArgs
        if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
}

function Get-ExportArgs([string]$SportId) {
    $exportArgs = @((Join-Path $PSScriptRoot "exporters\$SportId.py"))
    if ($Week) { $exportArgs += @('--week', $Week) }
    return $exportArgs
}

if ($Predict -and -not $Week -and $Sport -notin 'nhl', 'nba') { throw '-Predict requires -Week <n> for football' }

if ($Sport -eq 'nfl' -or $Sport -eq 'all') {
    $py = Join-Path $NflRepo '.venv\Scripts\python.exe'
    if ($Grade) {
        # (grade --all --refresh: every saved week, with the latest final scores)
        Invoke-Step "NFL: grade $Season (every saved week)" $py @("$Steps\nfl.py", 'step', 'grade', '--season', $Season) $NflRepo
    }
    if ($Predict) {
        Invoke-Step "NFL: predict $Season week $Week" $py @("$Steps\nfl.py", 'step', 'predict', '--season', $Season, '--week', $Week) $NflRepo
        Invoke-Step "NFL: season sim $Season" $py @("$Steps\nfl.py", 'step', 'season', '--season', $Season) $NflRepo
    }
    Invoke-Step 'NFL: export to site' $py (Get-ExportArgs 'nfl') $NflRepo
}

if ($Sport -eq 'cfb' -or $Sport -eq 'all') {
    $py = Join-Path $CfbRepo '.venv\Scripts\python.exe'
    if ($Grade) {
        Invoke-Step 'CFB: grade (every saved week)' $py @("$Steps\cfb.py", 'step', 'grade') $CfbRepo
    }
    if ($Predict) {
        Invoke-Step "CFB: project week $Week" $py @("$Steps\cfb.py", 'step', 'project', '--week', $Week) $CfbRepo
        Invoke-Step 'CFB: season records' $py @("$Steps\cfb.py", 'step', 'season') $CfbRepo
    }
    Invoke-Step 'CFB: export to site' $py (Get-ExportArgs 'cfb') $CfbRepo
}

if ($Sport -eq 'nhl' -or $Sport -eq 'all') {
    $py = Join-Path $NhlRepo '.venv\Scripts\python.exe'
    if ($Predict) {
        Invoke-Step 'NHL: daily predictions (next game day)' $py @("$Steps\nhl.py", 'step', 'daily') $NhlRepo
        Invoke-Step 'NHL: season sim' $py @("$Steps\nhl.py", 'step', 'simulate') $NhlRepo
        Invoke-Step 'NHL: power rankings' $py @("$Steps\nhl.py", 'step', 'power') $NhlRepo
    }
    # Daily slates: the adapter opens on the latest predicted day (no -Week).
    Invoke-Step 'NHL: export to site' $py @((Join-Path $PSScriptRoot 'exporters\nhl.py')) $NhlRepo
}

if ($Sport -eq 'nba' -or $Sport -eq 'all') {
    $py = Join-Path $NbaRepo '.venv\Scripts\python.exe'
    if ($Predict) {
        # The day's predictions are the model's own "NBA gameday" scheduled task's (desktop\sports\nba.py).
        Invoke-Step 'NBA: season sim' $py @("$Steps\nba.py", 'step', 'season') $NbaRepo
        Invoke-Step 'NBA: power rankings' $py @("$Steps\nba.py", 'step', 'power') $NbaRepo
    }
    Invoke-Step 'NBA: export to site' $py @((Join-Path $PSScriptRoot 'exporters\nba.py')) $NbaRepo
}

# The toolkit is stdlib-only, so whichever model Python ran last can check it all.
Invoke-Step 'Check site data' $py @((Join-Path $PSScriptRoot 'site_export.py'), 'check') $PSScriptRoot

Write-Host ""
Write-Host 'Data updated. Review the diff, then commit + push this repo to deploy.' -ForegroundColor Green
