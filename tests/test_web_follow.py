"""
The page's DOM-free modules, tested in Node with its built-in test runner (no
package.json, no npm install): tests/js/*.test.mjs (scroll following, the
player's subtitle choice). Skipped where Node is missing, except in CI
(GitHub's Linux and Windows runners have it), where a missing Node fails.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = sorted((Path(__file__).parent / "js").glob("*.test.mjs"))


@pytest.mark.parametrize("test_file", TESTS, ids=[p.stem for p in TESTS])
def test_page_module_in_node(test_file):
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail(f"Node is needed for {test_file.name} in CI")
        pytest.skip("Node is not installed")
    result = subprocess.run([node, "--test", "--test-reporter=tap", str(test_file)],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout


def test_no_package_install_is_needed():
    """Built-in modules only: a test that imports a package would need an install step in CI."""
    assert TESTS and not (Path(__file__).parent.parent / "package.json").exists()
    for path in TESTS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("import ") and " from " in line:
                assert '"node:' in line, (path.name, line)
