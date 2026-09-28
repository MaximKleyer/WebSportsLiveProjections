<#
One command to refresh the site's model data ("call both models").

  .\scripts\update-data.ps1                              # re-export the CSVs the models already have
  .\scripts\update-data.ps1 -Week 3                      # same, but open the week picker on week 3
  .\scripts\update-data.ps1 -Predict -Week 3             # run BOTH models for week 3, then export
  .\scripts\update-data.ps1 -Sport cfb -Predict -Week 3  # one sport only

-Predict also refreshes each model's season sim (can take a few minutes).
After it finishes: review the diff, then commit + push this repo to deploy.
#>
param(
    [ValidateSet('nfl', 'cfb', 'all')]
    [string]$Sport = 'all',
    [switch]$Predict,
    [int]$Week,
    [int]$Season = (Get-Date).Year
)

$ErrorActionPreference = 'Stop'
$NflRepo = 'C:\Users\maxim\NFLProjectionModel\nfl_projector_v1'
$CfbRepo = 'C:\Users\maxim\CFB_Projection_Model'

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

if ($Predict -and -not $Week) { throw '-Predict requires -Week <n>' }

if ($Sport -eq 'nfl' -or $Sport -eq 'all') {
    $py = Join-Path $NflRepo '.venv\Scripts\python.exe'
    if ($Predict) {
        Invoke-Step "NFL: predict $Season week $Week" $py @('-m', 'nfl_projector_v1', 'predict', '--season', $Season, '--week', $Week, '--players', '--plain') $NflRepo
        Invoke-Step "NFL: season sim $Season" $py @('-m', 'nfl_projector_v1', 'predict-season', '--season', $Season) $NflRepo
    }
    $exportArgs = @('scripts\export_web.py')
    if ($Week) { $exportArgs += @('--week', $Week) }
    Invoke-Step 'NFL: export to site' $py $exportArgs $NflRepo
}

if ($Sport -eq 'cfb' -or $Sport -eq 'all') {
    $py = Join-Path $CfbRepo '.venv\Scripts\python.exe'
    if ($Predict) {
        Invoke-Step "CFB: project week $Week" $py @('-m', 'cfb_model.project', '--week', $Week) $CfbRepo
        Invoke-Step 'CFB: season records' $py @('-m', 'cfb_model.season') $CfbRepo
    }
    $exportArgs = @('scripts\export_web.py')
    if ($Week) { $exportArgs += @('--week', $Week) }
    Invoke-Step 'CFB: export to site' $py $exportArgs $CfbRepo
}

Write-Host ""
Write-Host 'Data updated. Review the diff, then commit + push this repo to deploy.' -ForegroundColor Green
