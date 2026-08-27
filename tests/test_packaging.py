"""Packaging invariants — the things that break for consumers, not for us.

Every one of these has a failure mode that no other test would catch, because
they are all about what the INSTALLED package looks like rather than about what
the source tree does.
"""

from __future__ import annotations

import re
from pathlib import Path

import posthaste
from posthaste.http import SDK_VERSION

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PACKAGE_ROOT / "src" / "posthaste"


def test_py_typed_is_present() -> None:
    """Without this marker every annotation in the package is invisible.

    PEP 561: a type checker ignores a third-party package's types entirely
    unless the marker file is there. The package would still work and every
    caller would silently get `Any`, which is the whole reason for writing the
    annotations in the first place.
    """
    assert (SOURCE / "py.typed").is_file()


def test_the_wheel_would_actually_ship_py_typed() -> None:
    """A marker in the source tree that the build excludes helps nobody."""
    config = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["src/posthaste"]' in config


def test_the_version_is_stated_once_and_agrees_with_itself() -> None:
    config = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', config, re.MULTILINE)
    assert declared is not None
    assert declared.group(1) == SDK_VERSION == posthaste.__version__


def test_there_are_no_third_party_runtime_dependencies() -> None:
    """The promise the README makes, checked rather than remembered."""
    config = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^dependencies = \[\]", config, re.MULTILINE)


def test_the_package_imports_nothing_outside_the_standard_library() -> None:
    """Read with `ast`, not with a regex.

    A regex over the source counts the `from posthaste import Posthaste` in a
    module docstring as an import, which is how this check first went red on a
    package that imports nothing at all.
    """
    import ast

    allowed = {
        "__future__",
        "base64",
        "datetime",
        "email",
        "hashlib",
        "hmac",
        "json",
        "random",
        "re",
        "socket",
        "time",
        "typing",
        "urllib",
    }
    for path in sorted(SOURCE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in allowed, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                # `level > 0` is a relative import — inside this package.
                if node.level == 0 and node.module:
                    root = node.module.split(".")[0]
                    assert root in allowed, f"{path.name} imports {node.module}"


def test_every_module_parses_on_the_oldest_python_we_claim_to_support() -> None:
    """`requires-python = ">=3.9"` is a promise, and syntax is half of keeping it.

    A `match` statement, a `X | Y` annotation outside a `from __future__`
    module, or a `type` alias would install perfectly happily on 3.9 and then
    fail at import time for every caller on it — a failure no test running on a
    newer interpreter can see.

    This checks the syntax half mechanically. The library half (a stdlib API
    that only exists on a newer version) is covered by the CI matrix, which
    runs the whole suite on 3.9 as well as on the latest release.
    """
    import ast

    config = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    floor = re.search(r'requires-python = ">=(\d+)\.(\d+)"', config)
    assert floor is not None
    feature_version = (int(floor.group(1)), int(floor.group(2)))

    for path in sorted(SOURCE.glob("*.py")):
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=feature_version,
        )


def test_everything_in_dunder_all_actually_exists() -> None:
    """A stale `__all__` entry is an ImportError for anyone doing `from x import *`."""
    missing = [name for name in posthaste.__all__ if not hasattr(posthaste, name)]
    assert missing == []


def test_the_error_classes_are_all_exported() -> None:
    """A typed exception nobody can import cannot be caught by name."""
    from posthaste import errors

    for name in errors.__all__:
        if name[0].isupper() and name.endswith(("Error", "Limited", "Exhausted")):
            assert name in posthaste.__all__, name
