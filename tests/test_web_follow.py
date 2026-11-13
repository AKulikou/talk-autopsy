"""
The waiting screen's scroll following (talk_autopsy/web/static/follow.js), tested
in Node: tests/js/follow.test.mjs. Skipped where Node is missing, except in CI
(GitHub's Linux and Windows runners have it), where a missing Node fails.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TEST = Path(__file__).parent / "js" / "follow.test.mjs"


def test_follow_module_in_node():
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is needed for tests/js/follow.test.mjs in CI")
        pytest.skip("Node is not installed")
    result = subprocess.run([node, "--test", "--test-reporter=tap", str(TEST)],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout
