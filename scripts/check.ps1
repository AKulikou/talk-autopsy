# Runs exactly what CI runs (.github/workflows/ci.yml, job "test"), with every secret
# in the secret registry (talk_autopsy.secret_registry.ALL) removed from the environment, as on a CI runner. The
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
    # Identity: every commit not yet on origin/main must carry the GitHub noreply address as author and
    # committer (owner, 2026-11-14). Only hashes are printed, never the other address.
    $noreply = '332971863+AKulikou@users.noreply.github.com'
    Write-Host "> identity of the commits in origin/main..HEAD"
    $lines = @(git log 'origin/main..HEAD' '--format=%h %ae %ce')
    if ($LASTEXITCODE -ne 0) { throw 'could not list origin/main..HEAD (no origin/main?)' }
    $bad = @($lines | Where-Object { $_ } | Where-Object {
        $parts = $_.Split(' '); $parts[1] -ne $noreply -or $parts[2] -ne $noreply })
    if ($bad.Count -gt 0) {
        throw "commits with an author or committer email other than $noreply`: $(($bad | ForEach-Object { $_.Split(' ')[0] }) -join ', ')"
    }
    Write-Host "  $($lines.Count) commit(s) not pushed, all with the noreply address"
    # --inexact: keep optional extras installed here (the `align` extra, torch for the Gemini fallback);
    # CI syncs a fresh environment with `uv sync --locked`, so the checked packages are the same.
    Invoke-Step uv @('sync', '--locked', '--inexact')
    # Importing the registry reads neither .env nor a secret, so this is safe before the clear-out.
    $names = (uv run python -c "from talk_autopsy import secret_registry; print(' '.join(secret_registry.ALL))").Split(' ')
    if ($LASTEXITCODE -ne 0 -or -not $names) { throw 'could not read secret_registry.ALL' }
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
    # De-identification: the local, git-ignored name list and the committed patterns (CI runs the patterns only).
    Invoke-Step uv @('run', 'python', 'scripts/check_names.py')
}
finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
    Pop-Location
}
