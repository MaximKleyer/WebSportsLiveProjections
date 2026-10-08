<#
Puts a "Sports Models" shortcut on your desktop that opens the app.

  .\desktop\install-shortcut.ps1

Run it again any time; it just rewrites the shortcut. To pin the app, right-click
the shortcut and pick "Pin to Start" (or open it and pin its taskbar button).
The app is plain Python (the standard library) - no install, nothing to update.
#>
$ErrorActionPreference = 'Stop'
$app = Join-Path $PSScriptRoot 'app.py'
$icon = Join-Path $PSScriptRoot 'app.ico'

# The real interpreter (not the Microsoft Store alias), via the py launcher when there is one.
$python = $null
if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $python = (& py.exe -3 -c 'import sys; print(sys.executable)').Trim()
}
if (-not $python) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'  # no console window
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }

if (-not (Test-Path $icon)) {
    & $python (Join-Path $PSScriptRoot 'make_icon.py')
}

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'Sports Models.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$app`""
$shortcut.WorkingDirectory = Split-Path $PSScriptRoot -Parent
$shortcut.Description = 'Run the sports models and show the results'
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Save()

Write-Host "Shortcut created: $link" -ForegroundColor Green
Write-Host "It runs: $pythonw `"$app`""
