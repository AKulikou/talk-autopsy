# Runs exactly what CI runs (.github/workflows/ci.yml, job "test"), with every secret
# in config.SECRET_ENV_VARS removed from the environment, as on a CI runner. The
# removed values are restored when the script ends, so the calling shell keeps them.
# Run it before every push: pwsh scripts/check.ps1   (macOS/Linux: scripts/check.sh)
$ErrorActionPreference = 'Stop'

function Invoke-Step([string]$Program, [string[]]$Arguments) {
    Write-Host "> $Program $($Arguments -join ' ')"
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "failed (exit $LASTEXITCODE): $Program $($Arguments -join ' ')" }
}

Push-Location (Split-Path -Parent $PSScriptRoot)
$saved = @{}
try {
    Invoke-Step uv @('sync', '--locked')
    # Importing config reads neither .env nor a secret, so this is safe before the clear-out.
    $names = (uv run python -c "from talk_autopsy import config; print(' '.join(config.SECRET_ENV_VARS))").Split(' ')
    if ($LASTEXITCODE -ne 0 -or -not $names) { throw 'could not read config.SECRET_ENV_VARS' }
    foreach ($name in $names) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($null -ne $value) {
            $saved[$name] = $value
            [Environment]::SetEnvironmentVariable($name, $null)
        }
    }
    Write-Host "Cleared from the environment: $($names -join ', ')"
    Invoke-Step uv @('run', 'ruff', 'check', '.')
    Invoke-Step uv @('run', 'pytest')
}
finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
    Pop-Location
}
