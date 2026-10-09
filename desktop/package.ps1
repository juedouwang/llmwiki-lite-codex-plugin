#requires -Version 5.1
# Package the built desktop app (desktop/dist) as a release zip plus a SHA-256 file.
# Run desktop/build.ps1 first. Output: desktop/release/WildResearchWorkbench-<version>-windows-x64.zip
param([string] $Version)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$DesktopRoot = $PSScriptRoot
$RepoRoot = Split-Path -Parent $DesktopRoot
$AppName = 'WildResearchWorkbench'
$BundleRoot = Join-Path $DesktopRoot "dist\$AppName"
$ReleaseRoot = Join-Path $DesktopRoot 'release'

if (-not (Test-Path -LiteralPath (Join-Path $BundleRoot "$AppName.exe") -PathType Leaf)) {
    throw "No build found at $BundleRoot. Run desktop/build.ps1 first."
}
$manifest = Get-Content -LiteralPath (Join-Path $RepoRoot 'plugins\llmwiki-lite\.codex-plugin\plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$built = Get-Content -LiteralPath (Join-Path $BundleRoot '_internal\plugin\.codex-plugin\plugin.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($built.version -ne $manifest.version) {
    throw "The build is $($built.version) but the source is $($manifest.version). Rebuild first."
}
if (-not $Version) { $Version = $manifest.version }
$Version = $Version.TrimStart('v')
if ($Version -ne $manifest.version) { throw "Version $Version does not match plugin.json $($manifest.version)." }

$name = "$AppName-$Version-windows-x64"
$zip = Join-Path $ReleaseRoot "$name.zip"
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }

# Zip straight from the build with forward-slash entry names. No top folder: Explorer's
# "Extract All" already creates one named after the zip.
# (Compress-Archive in Windows PowerShell 5.1 writes backslash names and trips over
# files a virus scanner is still reading.)
$BuildPython = Join-Path $env:LOCALAPPDATA 'LLMWikiDesktop\build-env\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) { throw "Missing $BuildPython. Run desktop/build.ps1 first." }
$zipScript = @'
import sys
import zipfile
from pathlib import Path

target, pairs = Path(sys.argv[1]), sys.argv[2:]
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for source, name in zip(pairs[::2], pairs[1::2]):
        source = Path(source)
        if source.is_file():
            archive.write(source, name)
            continue
        for item in sorted(p for p in source.rglob("*") if p.is_file()):
            archive.write(item, f"{name}/{item.relative_to(source).as_posix()}")
'@
$entries = @(
    $BundleRoot, $AppName,
    (Join-Path $DesktopRoot 'install.ps1'), 'install.ps1',
    (Join-Path $DesktopRoot 'release-files\install.cmd'), 'install.cmd',
    (Join-Path $DesktopRoot 'release-files\README.txt'), 'README.txt',
    (Join-Path $RepoRoot 'LICENSE'), 'LICENSE'
)
# A file, not -c: Windows PowerShell 5.1 drops the double quotes inside native arguments.
$zipFile = Join-Path $ReleaseRoot '_make_zip.py'
[IO.File]::WriteAllText($zipFile, $zipScript, (New-Object Text.UTF8Encoding $false))
try {
    & $BuildPython -I -B $zipFile $zip @entries
    if ($LASTEXITCODE -ne 0) { throw 'Creating the zip failed.' }
} finally {
    Remove-Item -LiteralPath $zipFile -Force -ErrorAction SilentlyContinue
}
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$zip.sha256", "$hash  $name.zip`n", (New-Object Text.UTF8Encoding $false))
Write-Host "Packaged: $zip"
Write-Host "SHA-256:  $hash"
