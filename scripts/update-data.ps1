<#
One command to refresh the site's model data ("call both models").

  .\scripts\update-data.ps1                              # re-export the CSVs the models already have
  .\scripts\update-data.ps1 -Week 3                      # same, but open the week picker on week 3
  .\scripts\update-data.ps1 -Predict -Week 3             # run every model (football for week 3), then export
  .\scripts\update-data.ps1 -Sport cfb -Predict -Week 3  # one sport only
  .\scripts\update-data.ps1 -Sport nhl -Predict          # NHL: today's games, season sim, power rankings
  .\scripts\update-data.ps1 -Grade                       # grade the football models' finished games first
  .\scripts\update-data.ps1 -Grade -Predict -Week 5      # the weekly routine: grade, project week 5, export

-Predict also refreshes each model's season sim (can take a few minutes).
Football needs -Week with -Predict; the NHL predicts the next game day on its own.
-Grade runs each football model's own grader over every saved week (re-grading
a finished week reproduces it; a week in progress counts the games played so
far), before any -Predict. The NHL needs no grading step: its daily run pulls
in results and settles bets, and its adapter grades from them.
Each sport's adapter (scripts\exporters\<sport>.py) runs with that model's own
venv Python; then every data file is validated (scripts\site_export.py check).
After it finishes: review the diff, then commit + push this repo to deploy.
#>
param(
    [ValidateSet('nfl', 'cfb', 'nhl', 'all')]
    [string]$Sport = 'all',
    [switch]$Predict,
    [switch]$Grade,
    [int]$Week,
    [int]$Season = (Get-Date).Year
)

$ErrorActionPreference = 'Stop'
$NflRepo = 'C:\Users\maxim\NFLProjectionModel\nfl_projector_v1'
$CfbRepo = 'C:\Users\maxim\CFB_Projection_Model'
$NhlRepo = 'C:\Users\maxim\NHL_Projection_Model'

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

if ($Predict -and -not $Week -and $Sport -ne 'nhl') { throw '-Predict requires -Week <n> for football' }

if ($Sport -eq 'nfl' -or $Sport -eq 'all') {
    $py = Join-Path $NflRepo '.venv\Scripts\python.exe'
    if ($Grade) {
        # --refresh: fetch the latest final scores rather than a cached feed
        Invoke-Step "NFL: grade $Season (every saved week)" $py @('-m', 'nfl_projector_v1', 'grade', '--season', $Season, '--all', '--refresh') $NflRepo
    }
    if ($Predict) {
        Invoke-Step "NFL: predict $Season week $Week" $py @('-m', 'nfl_projector_v1', 'predict', '--season', $Season, '--week', $Week, '--players', '--plain') $NflRepo
        Invoke-Step "NFL: season sim $Season" $py @('-m', 'nfl_projector_v1', 'predict-season', '--season', $Season) $NflRepo
    }
    Invoke-Step 'NFL: export to site' $py (Get-ExportArgs 'nfl') $NflRepo
}

if ($Sport -eq 'cfb' -or $Sport -eq 'all') {
    $py = Join-Path $CfbRepo '.venv\Scripts\python.exe'
    if ($Grade) {
        Invoke-Step 'CFB: grade (every saved week)' $py @('-m', 'cfb_model.grade', '--all') $CfbRepo
    }
    if ($Predict) {
        Invoke-Step "CFB: project week $Week" $py @('-m', 'cfb_model.project', '--week', $Week) $CfbRepo
        Invoke-Step 'CFB: season records' $py @('-m', 'cfb_model.season') $CfbRepo
    }
    Invoke-Step 'CFB: export to site' $py (Get-ExportArgs 'cfb') $CfbRepo
}

if ($Sport -eq 'nhl' -or $Sport -eq 'all') {
    $py = Join-Path $NhlRepo '.venv\Scripts\python.exe'
    if ($Predict) {
        $nhl = Join-Path $NhlRepo '.venv\Scripts\nhl.exe'
        Invoke-Step 'NHL: daily predictions (next game day)' $nhl @('daily') $NhlRepo
        Invoke-Step 'NHL: season sim' $nhl @('simulate') $NhlRepo
        Invoke-Step 'NHL: power rankings' $nhl @('power') $NhlRepo
    }
    # Daily slates: the adapter opens on the latest predicted day (no -Week).
    Invoke-Step 'NHL: export to site' $py @((Join-Path $PSScriptRoot 'exporters\nhl.py')) $NhlRepo
}

# The toolkit is stdlib-only, so whichever model Python ran last can check it all.
Invoke-Step 'Check site data' $py @((Join-Path $PSScriptRoot 'site_export.py'), 'check') $PSScriptRoot

Write-Host ""
Write-Host 'Data updated. Review the diff, then commit + push this repo to deploy.' -ForegroundColor Green
