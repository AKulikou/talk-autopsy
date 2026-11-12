<#
.SYNOPSIS
Talk Autopsy launcher for Windows: no command line needed.

.DESCRIPTION
Started by "Talk Autopsy.bat" (double-click, a desktop shortcut, or a video file dropped
onto the .bat). It asks for a YouTube link or a video file and whether the video is
about smart contracts (--domain web3), checks the setup with `talk-autopsy doctor`,
shows the plan and the cost estimate (`run --dry-run`), asks to continue, runs the
pipeline with the TV file and the laptop files (--output mp4-burned,srt,ass), and opens
the output folder. The tool's own cost cap (--max-cost, 3 USD per video by default)
still applies. Everything the run prints is also written to logs\launcher\<time>.log.

Prompts on screen are in Russian (the owner's language); comments are in English.

The parameters let the launcher be tested without typing:
  -Source <url-or-path>  skip the first question (a file dropped on the .bat arrives here)
  -Web3 y|n              skip the second question
  -Yes                   answer "y" to "continue?"
  -DryRun                stop after the plan and the estimate: nothing is run or spent
  -NoPause               do not wait for Enter before the window closes
#>
param(
    [Parameter(Position = 0)][string]$Source = "",
    [ValidateSet("", "y", "n")][string]$Web3 = "",
    [switch]$Yes,
    [switch]$DryRun,
    [switch]$NoPause
)

# Native commands write progress to stderr; "Stop" would turn that into errors in Windows PowerShell 5.1.
$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location -LiteralPath $ProjectRoot
try { $Host.UI.RawUI.WindowTitle = "Talk Autopsy" } catch { }
# The CLI prints UTF-8 (titles, paths); unbuffered so progress shows line by line.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$Formats = "mp4-burned,srt,ass"
$Interactive = -not $PSBoundParameters.ContainsKey("Source")
# Problems doctor reports that do not matter for a local file.
$YouTubeOnlyChecks = @("JavaScript runtime", "yt-dlp-ejs")

function Wait-Enter {
    if (-not $NoPause) { [void](Read-Host "Нажмите Enter, чтобы закрыть окно") }
}

function Stop-Launcher([int]$Code) {
    Wait-Enter
    exit $Code
}

function Read-YesNo([string]$Prompt) {
    while ($true) {
        $answer = (Read-Host $Prompt).Trim().ToLowerInvariant()
        if ($answer -in @("y", "yes", "д", "да")) { return $true }
        if ($answer -in @("n", "no", "н", "нет")) { return $false }
        Write-Host "Ответьте y (да) или n (нет)."
    }
}

function ConvertTo-Source([string]$Text) {
    # A path dragged into the window, or copied with "Copy as path", arrives in quotes.
    $value = $Text.Trim()
    if ($value.StartsWith("& ")) { $value = $value.Substring(2).Trim() }
    return $value.Trim('"', "'").Trim()
}

function Test-Url([string]$Value) {
    return $Value -match '^https?://'
}

function Test-Source([string]$Value) {
    if (-not $Value) { return $false }
    if (Test-Url $Value) { return $true }
    return Test-Path -LiteralPath $Value -PathType Leaf
}

function Invoke-Cli([string[]]$CliArgs) {
    # `uv run talk-autopsy <args>`: stdout and stderr merged into plain text lines.
    & uv run --quiet talk-autopsy @CliArgs 2>&1 | ForEach-Object { "$_" }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Не найден uv (он запускает Talk Autopsy)." -ForegroundColor Red
    Write-Host "Установите: winget install astral-sh.uv, затем откройте ярлык снова."
    Stop-Launcher 1
}

Write-Host "Talk Autopsy: русские субтитры к английскому видео" -ForegroundColor Cyan
Write-Host ""

# 1. Source: a YouTube link or a local video file.
$Source = ConvertTo-Source $Source
while (-not (Test-Source $Source)) {
    if (-not $Interactive) {
        Write-Host "Файл не найден: $Source" -ForegroundColor Red
        Stop-Launcher 1
    }
    if ($Source) { Write-Host "Файл не найден: $Source" -ForegroundColor Red }
    $Source = ConvertTo-Source (Read-Host "Вставьте ссылку YouTube или путь к видеофайлу (можно перетащить файл в это окно)")
}
if (-not (Test-Url $Source)) { $Source = (Resolve-Path -LiteralPath $Source).Path }
Write-Host "Видео: $Source"

# 2. Domain glossary.
if ($Web3) { $IsWeb3 = $Web3 -eq "y" } else { $IsWeb3 = Read-YesNo "Видео про смарт-контракты? (y/n)" }
$DomainArgs = @()
if ($IsWeb3) { $DomainArgs = @("--domain", "web3") }

# 3. Setup check, quiet unless something is missing.
Write-Host "Проверка установки..."
$doctor = @(Invoke-Cli @("doctor"))
$doctorFailed = $LASTEXITCODE -ne 0
$problems = @()
for ($i = 0; $i -lt $doctor.Count; $i++) {
    if ($doctor[$i] -match '^\s+MISSING\s+(.+)$') {
        $check = $Matches[1]
        $youtubeOnly = @($YouTubeOnlyChecks | Where-Object { $check.StartsWith($_) }).Count -gt 0
        if ((Test-Url $Source) -or -not $youtubeOnly) {
            $problems += $doctor[$i]
            if ($i + 1 -lt $doctor.Count -and $doctor[$i + 1] -match '^\s+fix:') { $problems += $doctor[$i + 1] }
        }
    }
}
if ($doctorFailed -and $problems.Count -eq 0 -and -not ($doctor -match '^\s+MISSING')) {
    $problems = $doctor  # doctor itself failed: show everything it printed
}
if ($problems.Count -gt 0) {
    Write-Host "Не хватает того, что нужно для работы:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host $_ }
    Write-Host "Исправьте (строки fix: выше) и запустите снова."
    Stop-Launcher 1
}

# 4. Plan and estimate (nothing is downloaded, uploaded or spent).
Write-Host ""
Write-Host "План и оценка стоимости:" -ForegroundColor Cyan
$runArgs = @("run", $Source) + $DomainArgs + @("--output", $Formats)
Invoke-Cli ($runArgs + @("--dry-run")) | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Host "Запуск невозможен: см. сообщение Error выше." -ForegroundColor Red
    Stop-Launcher 1
}
Write-Host "Лимит: не больше 3 USD за видео, Claude и распознавание речи вместе (иначе программа остановится сама)."
if ($DryRun) {
    Write-Host "Пробный режим (-DryRun): обработка не запускалась."
    Stop-Launcher 0
}
if (-not $Yes -and -not (Read-YesNo "Продолжить? (y/n)")) {
    Write-Host "Отменено."
    Stop-Launcher 0
}

# 5. The run, shown as the CLI prints it and kept in a log file.
$logDir = Join-Path $ProjectRoot "logs\launcher"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
Write-Host ""
Write-Host "Обработка (окно можно не трогать; журнал: $log)" -ForegroundColor Cyan
$lines = New-Object System.Collections.Generic.List[string]
Invoke-Cli $runArgs | ForEach-Object {
    Write-Host $_
    $lines.Add($_)
    Add-Content -LiteralPath $log -Value $_ -Encoding UTF8
}
$code = $LASTEXITCODE

# 6. Result: open the output folder, or show where it went wrong.
Write-Host ""
if ($code -ne 0) {
    Write-Host "Ошибка (код $code). Последние строки журнала:" -ForegroundColor Red
    $lines | Select-Object -Last 15 | ForEach-Object { Write-Host "  $_" }
    Write-Host "Полный журнал: $log"
    Stop-Launcher $code
}
$folder = $null
foreach ($line in $lines) {
    if ($line -match '^Outputs in (.+):$') { $folder = $Matches[1] }
}
Write-Host "Готово." -ForegroundColor Green
if ($folder -and (Test-Path -LiteralPath $folder)) {
    Write-Host "Файлы: $folder"
    Write-Host "  *.ru.mp4 - для телевизора (субтитры в картинке); *.srt / *.ass - для ноутбука (VLC)."
    Start-Process explorer.exe -ArgumentList "`"$folder`""
} else {
    Write-Host "Папка с результатом не найдена в выводе; журнал: $log"
}
Stop-Launcher 0
