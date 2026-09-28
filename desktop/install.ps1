#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $SourceDirectory = (Join-Path $PSScriptRoot 'dist\WildResearchWorkbench')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This installer supports Windows only.' }
if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is not set.' }

$AppName = 'WildResearchWorkbench'
$ExeName = "$AppName.exe"
$ProgramsRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$InstallRoot = Join-Path $ProgramsRoot $AppName
$InstalledExe = Join-Path $InstallRoot $ExeName
$SourceRoot = [IO.Path]::GetFullPath($SourceDirectory).TrimEnd('\')
# ASCII source also works in Windows PowerShell 5.1 without relying on a BOM.
# The exact shortcut label is: U+91CE U+4EBA U+5DE5 U+4F5C U+53F0
# U+FF08 U+684C U+9762 U+7248 U+FF09 (full-width parentheses).
$ShortcutName = -join [char[]]@(0x91CE, 0x4EBA, 0x5DE5, 0x4F5C, 0x53F0,
    0xFF08, 0x684C, 0x9762, 0x7248, 0xFF09)

function Assert-NoReparsePoint([string] $Path, [switch] $Recurse) {
    $full = [IO.Path]::GetFullPath($Path)
    for ($current = $full; $current; $current = [IO.Path]::GetDirectoryName($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a symlink/junction: $current"
            }
        }
    }
    if ($Recurse -and (Test-Path -LiteralPath $full)) {
        foreach ($item in Get-ChildItem -LiteralPath $full -Force -Recurse) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a symlink/junction: $($item.FullName)"
            }
        }
    }
}

function Assert-ManagedPath([string] $Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $leaf = [IO.Path]::GetFileName($full)
    if ([IO.Path]::GetDirectoryName($full) -ne $ProgramsRoot -or
        ($leaf -ne $AppName -and $leaf -notmatch '^\.WildResearchWorkbench\.(install|backup)-[0-9a-f]{32}$')) {
        throw "Refusing an operation outside the generated application directories: $full"
    }
    Assert-NoReparsePoint $full -Recurse
}

function Remove-GeneratedDirectory([string] $Path) {
    # Only disposable GUID-named staging/backup trees may be recursively deleted.
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    Assert-ManagedPath $full
    if ($full -eq $InstallRoot) { throw 'Refusing to delete the installed application directly.' }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Assert-Bundle([string] $Path) {
    foreach ($relative in @($ExeName, '_internal\plugin\scripts\web_server.py',
        '_internal\plugin\scripts\static\workbench.ico', '_internal\plugin\templates\weekly-report.md')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Path $relative) -PathType Leaf)) {
            throw "Incomplete onedir bundle: missing $relative in $Path"
        }
    }
}

Assert-NoReparsePoint $SourceRoot -Recurse
Assert-Bundle $SourceRoot
Assert-ManagedPath $InstallRoot
if ($SourceRoot -eq $InstallRoot -or
    $SourceRoot.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $InstallRoot.StartsWith($SourceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The source and installation directories must not overlap.'
}
if (Get-Process -Name $AppName -ErrorAction SilentlyContinue) {
    throw 'Close all WildResearchWorkbench windows before installing. No process was stopped.'
}
if (Test-Path -LiteralPath $InstallRoot) {
    Assert-Bundle $InstallRoot  # Never replace an unrelated pre-existing directory.
}

$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$startMenu = [Environment]::GetFolderPath('Programs')
if (-not $desktop -or -not $startMenu) { throw 'Cannot locate the current user shortcut folders.' }
$shortcutPaths = @((Join-Path $desktop "$ShortcutName.lnk"), (Join-Path $startMenu "$ShortcutName.lnk"))
if (-not $PSCmdlet.ShouldProcess($InstallRoot, 'Install desktop bundle and desktop/start-menu shortcuts')) {
    return
}

$shell = New-Object -ComObject WScript.Shell
try {
    # Only our distinct desktop-app shortcuts can be updated; website links stay intact.
    foreach ($path in $shortcutPaths) {
        if (Test-Path -LiteralPath $path) {
            $shortcut = $shell.CreateShortcut($path)
            try {
                if ($shortcut.TargetPath -ne $InstalledExe) {
                    throw "An unrelated shortcut already uses the desktop-app name: $path"
                }
            } finally {
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
            }
        }
    }

    $id = [Guid]::NewGuid().ToString('N')
    $stage = Join-Path $ProgramsRoot ".WildResearchWorkbench.install-$id"
    $backup = Join-Path $ProgramsRoot ".WildResearchWorkbench.backup-$id"
    Assert-ManagedPath $stage
    Assert-ManagedPath $backup
    if ((Test-Path -LiteralPath $stage) -or (Test-Path -LiteralPath $backup)) {
        throw 'The generated staging or backup directory already exists; retry the installer.'
    }
    [void](New-Item -ItemType Directory -Path $stage)
    $oldMoved = $false
    try {
        foreach ($item in Get-ChildItem -LiteralPath $SourceRoot -Force) {
            Copy-Item -LiteralPath $item.FullName -Destination $stage -Recurse -Force
        }
        Assert-Bundle $stage
        # Recheck immediately before renaming; both ends must be direct children
        # of the fixed Programs root, with no junctions in either tree.
        Assert-ManagedPath $InstallRoot
        Assert-ManagedPath $stage
        Assert-ManagedPath $backup
        if (Test-Path -LiteralPath $InstallRoot) {
            Move-Item -LiteralPath $InstallRoot -Destination $backup
            $oldMoved = $true
        }
        try {
            Move-Item -LiteralPath $stage -Destination $InstallRoot
        } catch {
            if ($oldMoved -and -not (Test-Path -LiteralPath $InstallRoot)) {
                Assert-ManagedPath $backup
                Assert-ManagedPath $InstallRoot
                Move-Item -LiteralPath $backup -Destination $InstallRoot
                $oldMoved = $false
            }
            throw
        }
    } finally {
        if (Test-Path -LiteralPath $stage) {
            Remove-GeneratedDirectory $stage
        }
    }

    foreach ($path in $shortcutPaths) {
        $parent = [IO.Path]::GetDirectoryName($path)
        if (-not (Test-Path -LiteralPath $parent)) {
            [void](New-Item -ItemType Directory -Path $parent)
        }
        $shortcut = $shell.CreateShortcut($path)
        try {
            $shortcut.TargetPath = $InstalledExe
            $shortcut.WorkingDirectory = $InstallRoot
            $shortcut.Arguments = ''
            $shortcut.IconLocation = "$InstalledExe,0"
            $shortcut.Description = $ShortcutName
            $shortcut.WindowStyle = 1
            $shortcut.Save()
        } finally {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
        }
    }
    if ($oldMoved) {
        try { Remove-GeneratedDirectory $backup }
        catch { Write-Warning "Installed successfully; old program backup retained at $backup. $($_.Exception.Message)" }
    }
} finally {
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
}

Write-Host "Installed: $InstalledExe"
Write-Host "Shortcuts: $ShortcutName"
Write-Host 'User data and website shortcuts were not changed. No startup entry was created; the app was not launched.'
