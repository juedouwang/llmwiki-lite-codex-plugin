#requires -Version 5.1
[CmdletBinding()]
param([string] $Python = 'python')

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This build supports Windows only.' }
if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is not set.' }

$DesktopRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$BuildEnv = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'LLMWikiDesktop\build-env'))
$BuildPython = Join-Path $BuildEnv 'Scripts\python.exe'
$WorkRoot = Join-Path $DesktopRoot 'build'
$DistRoot = Join-Path $DesktopRoot 'dist'
$BundleRoot = Join-Path $DistRoot 'WildResearchWorkbench'
$CacheRoot = Join-Path $WorkRoot 'pyinstaller-cache'

function Assert-NoReparsePoint([string] $Path, [switch] $Recurse) {
    $full = [IO.Path]::GetFullPath($Path)
    for ($current = $full; $current; $current = [IO.Path]::GetDirectoryName($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a symlink/junction in a generated path: $current"
            }
        }
    }
    if ($Recurse -and (Test-Path -LiteralPath $full)) {
        foreach ($item in Get-ChildItem -LiteralPath $full -Force -Recurse) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a symlink/junction in a generated tree: $($item.FullName)"
            }
        }
    }
}

function Invoke-Checked([string] $Executable, [string[]] $ArgumentList) {
    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable"
    }
}

# These are fixed generated paths, never user data or an arbitrary output root.
# PyInstaller --noconfirm/--clean may remove their existing generated contents.
foreach ($path in @($WorkRoot, $DistRoot)) {
    $full = [IO.Path]::GetFullPath($path)
    if ([IO.Path]::GetDirectoryName($full) -ne $DesktopRoot) {
        throw "Generated directory is outside desktop/: $full"
    }
    Assert-NoReparsePoint $full -Recurse
}
Assert-NoReparsePoint $BuildEnv

foreach ($name in @('app.py', 'Workbench.spec', 'requirements-build.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot $name) -PathType Leaf)) {
        throw "Missing desktop/$name. Complete the desktop sources before building."
    }
}
if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    if ((Test-Path -LiteralPath $BuildEnv) -and
        @(Get-ChildItem -LiteralPath $BuildEnv -Force).Count -gt 0) {
        throw "The build environment is incomplete/in use; do not overwrite it: $BuildEnv"
    }
    Invoke-Checked $Python @('-I', '-B', '-m', 'venv', $BuildEnv)
}

# Reject an unrelated/global interpreter or a reused environment of the wrong ABI.
$checkEnvironment = @'
import pathlib, struct, sys
root = pathlib.Path(sys.argv[1]).resolve()
assert sys.version_info[:2] == (3, 11), 'Python 3.11 is required'
assert struct.calcsize('P') == 8, '64-bit Python is required'
assert sys.prefix != sys.base_prefix, 'A virtual environment is required'
assert pathlib.Path(sys.prefix).resolve() == root, 'Unexpected virtual environment'
cfg = (root / 'pyvenv.cfg').read_text(encoding='utf-8').lower()
assert 'include-system-site-packages = false' in cfg, 'System packages must be isolated'
'@
Invoke-Checked $BuildPython @('-I', '-B', '-c', $checkEnvironment, $BuildEnv)
Invoke-Checked $BuildPython @('-I', '-B', '-m', 'pip', '--isolated', '--require-virtualenv',
    '--disable-pip-version-check', 'install', '--requirement', (Join-Path $DesktopRoot 'requirements-build.txt'))
Invoke-Checked $BuildPython @('-I', '-B', '-m', 'pip', '--isolated', '--require-virtualenv', 'check')

# Keep PyInstaller's disposable cache out of the global Python/host environment.
$previousCache = $env:PYINSTALLER_CONFIG_DIR
try {
    $env:PYINSTALLER_CONFIG_DIR = $CacheRoot
    Invoke-Checked $BuildPython @('-I', '-B', '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--workpath', $WorkRoot, '--distpath', $DistRoot, (Join-Path $DesktopRoot 'Workbench.spec'))
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $previousCache
}

foreach ($relative in @('WildResearchWorkbench.exe', '_internal\plugin\scripts\web_server.py',
    '_internal\plugin\scripts\static\workbench.ico', '_internal\plugin\templates\weekly-report.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $BundleRoot $relative) -PathType Leaf)) {
        throw "The build is incomplete; missing $relative"
    }
}
Write-Host "Built: $(Join-Path $BundleRoot 'WildResearchWorkbench.exe')"
Write-Host 'Keep the whole onedir folder together. Run desktop/install.ps1 to install; nothing was installed or launched.'
