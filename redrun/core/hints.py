"""Deterministic hint extraction from log text. No model, no guessing.

Every function here is a pure string -> value mapping so the evidence tables built
from it are reproducible run to run.
"""

import re

# Stdlib modules are not packages: a missing one is a config/version problem, not a
# dependency bump, so they must not become package hints.
STDLIB = {
    "abc", "argparse", "asyncio", "base64", "collections", "contextlib", "copy", "csv",
    "dataclasses", "datetime", "decimal", "enum", "functools", "glob", "gzip", "hashlib",
    "http", "importlib", "inspect", "io", "itertools", "json", "logging", "math", "os",
    "pathlib", "pickle", "platform", "pprint", "queue", "random", "re", "secrets",
    "shutil", "signal", "socket", "sqlite3", "ssl", "statistics", "string", "struct",
    "subprocess", "sys", "tempfile", "textwrap", "threading", "time", "traceback",
    "types", "typing", "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
    "zipfile", "zlib",
}

_PKG_PATTERNS = (
    re.compile(r"No module named '([A-Za-z0-9_.\-]+)'"),
    re.compile(r"cannot import name '[^']+' from '([A-Za-z0-9_.\-]+)'"),
    re.compile(r"Package `([A-Za-z0-9_.\-]+)\s*(?:==|>=|<=|~=|!=|<|>)\s*[\d*]"),
    re.compile(r"Downloading ([A-Za-z0-9_.\-]+)-\d"),
    re.compile(r"Collecting ([A-Za-z0-9_.\-]+)"),
    re.compile(r"Successfully installed ([A-Za-z0-9_.\-]+)-\d"),
    re.compile(r"Could not find a version that satisfies the requirement ([A-Za-z0-9_.\-]+)"),
    re.compile(r"no matching distribution found for ([A-Za-z0-9_.\-]+)"),
    re.compile(r"cannot resolve\s+([A-Za-z0-9_.\-]+)"),
)

_FILE_IN_TRACEBACK = re.compile(r'File "([^"]+)", line \d+')
# A path only counts as a hint when it carries a line/col or pytest node suffix,
# so prose like "changed .github/workflows/ci.yml" names a file, not a location.
_FILE_WITH_LOCATION = re.compile(
    r"([A-Za-z0-9_./\-]+\.(?:py|pyi|ts|tsx|js|jsx|go|rs|java|rb|kt|swift|php|c|cc|cpp|h))"
    r"(?::\d+|::)",
)
_ERROR_CLASS = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\b")

_WORKDIR_PREFIXES = (
    re.compile(r"^/app/"),
    re.compile(r"^/home/runner/work/[^/]+/[^/]+/"),
    re.compile(r"^/github/workspace/"),
    re.compile(r"^[A-Za-z]:\\\\[^\\\\]+\\\\"),
)


def _strip_workdir(path: str) -> str:
    for prefix in _WORKDIR_PREFIXES:
        path = prefix.sub("", path)
    return path.lstrip("/")


def package_hint(text: str) -> str | None:
    """The package a line blames, if any. Lowercased, stdlib excluded."""
    for pattern in _PKG_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = match.group(1).split(".")[0].strip().lower()
        if not name or name in STDLIB:
            continue
        return name
    return None


def file_hint(text: str) -> str | None:
    """A repo-relative source path, only from traceback/pytest-shaped text."""
    hints = all_file_hints(text)
    return hints[0] if hints else None


def all_file_hints(text: str) -> list[str]:
    """Every repo-relative source path in error position, in order of appearance."""
    found: list[tuple[int, str]] = []
    for pattern in (_FILE_IN_TRACEBACK, _FILE_WITH_LOCATION):
        for match in pattern.finditer(text):
            found.append((match.start(), _strip_workdir(match.group(1))))
    ordered: list[str] = []
    for _, path in sorted(found):
        if path not in ordered:
            ordered.append(path)
    return ordered


def error_class(text: str) -> str | None:
    """The last exception class named on a line, e.g. AttributeError."""
    matches = _ERROR_CLASS.findall(text)
    return matches[-1] if matches else None
