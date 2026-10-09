#requires -Version 5.1
# Development mode: run the desktop app straight from this checkout. Code edits take
# effect the next time it starts; nothing is built or installed. It reuses the
# isolated environment that desktop/build.ps1 creates (run that once first).
param(
    [string] $DataHome,   # another data directory, e.g. a scratch copy for testing
    [switch] $Console     # stay attached and print errors instead of opening silently
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'The desktop app supports Windows only.' }

$BuildEnv = Join-Path $env:LOCALAPPDATA 'LLMWikiDesktop\build-env'
$Python = if ($Console) { Join-Path $BuildEnv 'Scripts\python.exe' } else { Join-Path $BuildEnv 'Scripts\pythonw.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing $Python. Run desktop/build.ps1 once to create the build environment."
}
# One data directory opens one window: a second start only focuses the first one,
# so an installed copy that is already open would keep showing its old code.
if (-not $DataHome -and (Get-Process -Name 'WildResearchWorkbench' -ErrorAction SilentlyContinue)) {
    throw 'The installed app is open on the same data. Close it, then run this again.'
}

$arguments = @('-B', ('"{0}"' -f (Join-Path $PSScriptRoot 'app.py')))
if ($DataHome) { $arguments += @('--home', ('"{0}"' -f $DataHome)) }
if ($Console) {
    & $Python -B (Join-Path $PSScriptRoot 'app.py') $(if ($DataHome) { '--home'; $DataHome })
    exit $LASTEXITCODE
}
Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
Write-Host 'Started the desktop app from source. Restart it to pick up code changes.'
