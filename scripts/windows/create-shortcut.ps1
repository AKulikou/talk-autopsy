<#
.SYNOPSIS
Creates (or replaces) the desktop shortcut "Talk Autopsy" for the current user.

.DESCRIPTION
The shortcut starts scripts\windows\Talk Autopsy.bat with the project folder as its
working directory. Run once: pwsh -File scripts\windows\create-shortcut.ps1
#>
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$target = Join-Path $PSScriptRoot "Talk Autopsy.bat"
$desktop = [Environment]::GetFolderPath("Desktop")  # the real desktop, also when OneDrive moves it
$path = Join-Path $desktop "Talk Autopsy.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = "Talk Autopsy: subtitles for a YouTube link or a video file"
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,18"  # a video icon
$shortcut.Save()
Write-Host "Shortcut: $path -> $target (working directory $projectRoot)"
