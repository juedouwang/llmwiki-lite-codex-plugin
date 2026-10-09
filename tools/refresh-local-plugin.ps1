#requires -Version 5.1
# Development: reinstall this checkout's plugin into the local Claude Code and Codex.
# Hosts only reinstall when the version changes, so the manifests carry a temporary
# "<next patch>-dev.<UTC time>" version during the refresh and are restored byte for
# byte afterwards; the committed version stays a clean release number.
# Requires both hosts' marketplace "llmwiki-lite" to point at this checkout.
param([switch] $SkipClaude, [switch] $SkipCodex)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Manifests = @(
    (Join-Path $RepoRoot 'plugins\llmwiki-lite\.codex-plugin\plugin.json'),
    (Join-Path $RepoRoot 'plugins\llmwiki-lite\.claude-plugin\plugin.json')
)
$Original = @{}
foreach ($path in $Manifests) { $Original[$path] = [IO.File]::ReadAllBytes($path) }

$text = [Text.Encoding]::UTF8.GetString($Original[$Manifests[0]])
$release = ($text | ConvertFrom-Json).version
if ($release -notmatch '^(\d+)\.(\d+)\.(\d+)$') { throw "plugin.json version must be X.Y.Z, found '$release'." }
$dev = '{0}.{1}.{2}-dev.{3}' -f $Matches[1], $Matches[2], ([int]$Matches[3] + 1), (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')

function Invoke-Host([string] $Executable, [string[]] $ArgumentList) {
    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "Failed: $Executable $($ArgumentList -join ' ')" }
}

try {
    foreach ($path in $Manifests) {
        $content = [Text.Encoding]::UTF8.GetString($Original[$path])
        $updated = $content -replace ('"version"(\s*):(\s*)"' + [regex]::Escape($release) + '"'), ('"version"$1:$2"' + $dev + '"')
        if ($updated -eq $content) { throw "No version field found in $path" }
        [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes($updated))
    }
    if (-not $SkipClaude) {
        Invoke-Host 'claude' @('plugin', 'marketplace', 'update', 'llmwiki-lite')
        Invoke-Host 'claude' @('plugin', 'update', 'llmwiki-lite@llmwiki-lite')
    }
    if (-not $SkipCodex) {
        Invoke-Host 'codex' @('plugin', 'add', 'llmwiki-lite@llmwiki-lite')
    }
} finally {
    foreach ($path in $Manifests) { [IO.File]::WriteAllBytes($path, $Original[$path]) }
}
Write-Host "Installed $dev locally. Start a new conversation to load it."
