"""Deterministic hint extraction: no model, no guessing, just patterns."""

import pytest

from redrun.core.hints import error_class, file_hint, package_hint


@pytest.mark.parametrize(
    "line,expected",
    [
        ("ModuleNotFoundError: No module named 'fastapi'", "fastapi"),
        ("ImportError: cannot import name 'RunInfo' from 'redrun.core.models'", "redrun"),
        ("Package `fastapi==0.116.0` requires `httpx<0.28`", "fastapi"),
        ("Downloading fastapi-0.116.0-py3-none-any.whl", "fastapi"),
        ("Successfully installed fastapi-0.116.0 httpx-0.28.1", "fastapi"),
        ("ERROR: Could not find a version that satisfies the requirement httpx>=0.28", "httpx"),
        ("no matching distribution found for uvloop", "uvloop"),
        ("ResolutionImpossible: cannot resolve pydantic-core", "pydantic-core"),
        ("No module named 'numpy.linalg'", "numpy"),
    ],
)
def test_package_hint_extraction(line: str, expected: str) -> None:
    assert package_hint(line) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ('  File "/app/redrun/collectors/github_runs.py", line 88, in collect', "redrun/collectors/github_runs.py"),
        ("redrun/collectors/github_runs.py:91: AttributeError", "redrun/collectors/github_runs.py"),
        ("FAILED tests/test_collectors.py::test_x - AssertionError", "tests/test_collectors.py"),
        ("at /home/runner/work/widgets/widgets/src/app.ts:14:3", "src/app.ts"),
        ("changed .github/workflows/ci.yml", None),
    ],
)
def test_file_hint_extraction(line: str, expected: str | None) -> None:
    assert file_hint(line) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("AttributeError: 'NoneType' object has no attribute 'created_at'", "AttributeError"),
        ("ModuleNotFoundError: No module named 'fastapi'", "ModuleNotFoundError"),
        ("no solution: version solving failed", None),
        ("401 Unauthorized", None),
    ],
)
def test_error_class_extraction(line: str, expected: str | None) -> None:
    assert error_class(line) == expected


def test_package_hint_is_none_when_absent() -> None:
    assert package_hint("Process completed with exit code 1.") is None


def test_package_hint_ignores_stdlib_modules() -> None:
    assert package_hint("ModuleNotFoundError: No module named 'os'") is None
