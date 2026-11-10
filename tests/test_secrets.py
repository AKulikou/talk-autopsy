"""
The secret registry (config.SECRET_ENV_VARS) is complete: every secret the
source reads from the environment is in it, and .env.example lists exactly the
registry. A new provider's key without a registry entry fails here, locally,
before a push; with an entry, conftest gives every test a placeholder for it.
"""

import ast
import os
import re

import pytest

from talk_autopsy import config
from tests.conftest import placeholder

SOURCE_DIRS = [config.PROJECT_ROOT / "talk_autopsy", config.PROJECT_ROOT / "scripts"]
SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*_(KEY|TOKEN|SECRET)")

# Calls whose first argument names an environment variable: os.getenv, environ.get
# and friends, and the config helpers that read one.
READ_CALLS = {"getenv", "require_key", "_setting"}
ENVIRON_METHODS = {"get", "pop", "setdefault"}

# Reads whose name is a function parameter, so the scan cannot resolve it. Each is
# checked another way: require_key accepts only OPTIONAL_KEYS (tested below);
# _setting's callers pass literals, which the scan sees as reads.
DYNAMIC_READS_ALLOWED = {("config.py", "require_key"), ("config.py", "_setting")}


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "environ") or (
        isinstance(node, ast.Attribute) and node.attr == "environ")


def env_reads(source: str) -> list[tuple[int, str | None, str]]:
    """
    (line, variable name or None if not resolvable, enclosing function) for every
    environment read in `source`. Names resolve from string literals, module-level
    string constants and config's attributes (config.API_KEY_ENV_VAR).
    """
    tree = ast.parse(source)
    constants = {
        target.id: node.value.value
        for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets if isinstance(target, ast.Name)
    }

    def resolve(arg: ast.AST) -> str | None:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.Name):
            return constants.get(arg.id) or (
                getattr(config, arg.id) if isinstance(getattr(config, arg.id, None), str) else None)
        if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and arg.value.id == "config":
            value = getattr(config, arg.attr, None)
            return value if isinstance(value, str) else None
        return None

    reads: list[tuple[int, str | None, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function = node.name
        arg = None
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else "")
            if name in READ_CALLS or (name in ENVIRON_METHODS and isinstance(func, ast.Attribute)
                                      and _is_environ(func.value)):
                arg = node.args[0]
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):
            arg = node.slice
        if arg is not None:
            reads.append((node.lineno, resolve(arg), function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return reads


def _source_files():
    for directory in SOURCE_DIRS:
        if directory.is_dir():
            yield from sorted(directory.rglob("*.py"))


def test_every_secret_the_source_reads_is_registered():
    unregistered, unresolved = [], []
    for path in _source_files():
        for line, name, function in env_reads(path.read_text(encoding="utf-8")):
            where = f"{path.relative_to(config.PROJECT_ROOT).as_posix()}:{line}"
            if name is None:
                if (path.name, function) not in DYNAMIC_READS_ALLOWED:
                    unresolved.append(where)
            elif SECRET_NAME.fullmatch(name) and name not in config.SECRET_ENV_VARS:
                unregistered.append(f"{where} {name}")
    assert not unregistered, f"add these to config.SECRET_ENV_VARS and .env.example: {unregistered}"
    assert not unresolved, f"environment reads the scan cannot name; use a literal: {unresolved}"


def test_the_scan_finds_reads_of_every_form():
    # The guard's own control: each form of a read is seen and named.
    source = '''
import os
from os import environ, getenv
from talk_autopsy import config
NAME = "CONSTANT_TOKEN"
a = os.environ["SUBSCRIPT_KEY"]
b = os.environ.get("GET_SECRET")
c = os.getenv(NAME)
d = getenv("BARE_GETENV_KEY")
e = environ.setdefault("BARE_ENVIRON_KEY", "")
def start():
    return config.require_key("HELPER_API_KEY"), os.getenv(config.API_KEY_ENV_VAR)
def dynamic(name):
    return os.getenv(name)
'''
    reads = env_reads(source)
    assert [name for _, name, _ in reads] == [
        "SUBSCRIPT_KEY", "GET_SECRET", "CONSTANT_TOKEN", "BARE_GETENV_KEY", "BARE_ENVIRON_KEY",
        "HELPER_API_KEY", "ANTHROPIC_API_KEY", None]
    assert reads[-1][2] == "dynamic"


def test_the_keys_config_checks_are_registered():
    assert config.API_KEY_ENV_VAR in config.SECRET_ENV_VARS
    assert set(config.OPTIONAL_KEYS) <= set(config.SECRET_ENV_VARS)


def _env_example() -> list[str]:
    return (config.PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()


def test_env_example_lists_exactly_the_registry_with_empty_values_and_a_comment():
    lines = _env_example()
    secrets = {}
    for number, line in enumerate(lines):
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if match and SECRET_NAME.fullmatch(match[1]):
            secrets[match[1]] = (match[2], lines[number - 1] if number else "")
    assert sorted(secrets) == sorted(config.SECRET_ENV_VARS)
    for name, (value, previous) in secrets.items():
        assert value == "", f"{name} must be empty in .env.example"
        assert previous.startswith("#"), f"{name} needs a comment line above it in .env.example"


@pytest.mark.parametrize("name", config.SECRET_ENV_VARS)
def test_every_secret_is_a_placeholder_inside_a_test(name):
    assert os.environ[name] == placeholder(name)
    assert "not-a-real-secret" in os.environ[name]
