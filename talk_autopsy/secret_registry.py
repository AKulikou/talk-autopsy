"""
The full secret registry: `config.SECRET_ENV_VARS` (the pipeline's keys) plus the
keys of evaluation tooling added after the `pipeline-v3` freeze.

`config.py` is a frozen path of `pipeline-v3` (docs/experiments.md, Freeze
pipeline-v3), so its registry cannot grow without a new tag and a new hold-out.
A key that only evaluation tooling reads is registered here instead. The checks
that guard secrets use `ALL`: the test placeholders (tests/conftest.py), the scan
for unregistered reads and the `.env.example` check (tests/test_secrets.py), and
the CI scripts that clear secrets (scripts/check.ps1, scripts/check.sh).
`config.key_is_set` and `doctor` know only the pipeline's keys.
"""

from talk_autopsy import config

EVALUATION_SECRET_ENV_VARS: tuple[str, ...] = (
    "GOOGLE_TRANSLATE_API_KEY",  # Whisper + Google Translate baseline (baselines/google_translate.py)
)

ALL: tuple[str, ...] = config.SECRET_ENV_VARS + EVALUATION_SECRET_ENV_VARS
