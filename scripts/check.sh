#!/usr/bin/env bash
# Runs exactly what CI runs (.github/workflows/ci.yml, job "test"), with every secret
# in config.SECRET_ENV_VARS removed from the environment, as on a CI runner.
# Run it before every push. Windows: scripts/check.ps1.
set -euo pipefail
cd "$(dirname "$0")/.."

# --inexact: keep optional extras installed here (the align extra, torch for the Gemini fallback);
# CI syncs a fresh environment with uv sync --locked, so the checked packages are the same.
uv sync --locked --inexact
# Importing config reads neither .env nor a secret, so this is safe before the clear-out.
secrets=$(uv run python -c "from talk_autopsy import config; print(' '.join(config.SECRET_ENV_VARS))" | tr -d '\r')
for name in $secrets; do
  unset "$name"
done
uv run ruff check .
uv run pytest
