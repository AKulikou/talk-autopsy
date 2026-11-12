<#
.SYNOPSIS
Creates (or replaces) the desktop shortcuts for the current user.

.DESCRIPTION
"Talk Autopsy" starts the web page (scripts\windows\Talk Autopsy Web.bat: the local server
plus the browser; closing its window stops the server). "Talk Autopsy (console)" keeps the
console launcher (scripts\windows\Talk Autopsy.bat), which also handles playlists and a video
file dropped onto it. Both start in the project folder. Run once:
pwsh -File scripts\windows\create-shortcut.ps1
#>
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$desktop = [Environment]::GetFolderPath("Desktop")  # the real desktop, also when OneDrive moves it
$shell = New-Object -ComObject WScript.Shell

function New-Shortcut([string]$Name, [string]$Target, [string]$Description, [string]$Icon) {
    $path = Join-Path $desktop "$Name.lnk"
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = $Description
    $shortcut.IconLocation = $Icon
    $shortcut.Save()
    Write-Host "Shortcut: $path -> $Target (working directory $projectRoot)"
}

New-Shortcut "Talk Autopsy" (Join-Path $PSScriptRoot "Talk Autopsy Web.bat") `
    "Talk Autopsy: subtitles for a YouTube link or a video file (web page)" `
    "$env:SystemRoot\System32\imageres.dll,18"  # a video icon
New-Shortcut "Talk Autopsy (console)" (Join-Path $PSScriptRoot "Talk Autopsy.bat") `
    "Talk Autopsy in the console: also playlists, and a video file dropped onto it" `
    "$env:SystemRoot\System32\cmd.exe,0"
