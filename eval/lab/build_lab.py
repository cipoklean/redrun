"""Build cipoklean/redrun-lab: a tiny package whose real runs are RedRun's judgment set.

Every constructed case is one branch with two commits:

  green  the case's files land and the workflow passes
  red    the breaking commit, which fails for exactly one reason

The prompt for this phase says the evaluation must MEASURE the pipeline, not tune
it. So this file only creates runs and records their ids; it never touches the
pipeline, the rules or the prompts.

    python eval/lab/build_lab.py main      # create the repo and push green main
    python eval/lab/build_lab.py green     # push every green commit, wait for green runs
    python eval/lab/build_lab.py red       # push every breaking commit, rerun the flakes
    python eval/lab/build_lab.py status    # print what was recorded

State lives in eval/lab/lab_runs.json. Credentials come from the ambient git
credential store and GITHUB_TOKEN; nothing is written to disk here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OWNER = "cipoklean"
REPO = "redrun-lab"
ROOT = Path(__file__).resolve().parents[2]
STATE = Path(__file__).resolve().parent / "lab_runs.json"
WORK = ROOT / ".cache" / "lab-work"
SSH_KEY = ROOT / ".cache" / "labkeys" / "lab_ed25519"
REMOTE_HTTPS = f"https://github.com/{OWNER}/{REPO}.git"
REMOTE_SSH = f"git@github.com:{OWNER}/{REPO}.git"

CI = """name: ci

on:
  push:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '{python}'
      - name: install
        run: python -m pip install --upgrade pip && python -m pip install -r requirements.txt
      - name: test
        run: python -m pytest -q
"""

README = """# redrun-lab

A deliberately tiny Python package. Its only job is to produce **real** GitHub
Actions runs, green and then red, so that RedRun's evaluation set is built from
runs that actually happened instead of hand-written fixtures.

Each case is a branch with two commits: a green one, and then the breaking
commit. `main` is green and stays green.

| branch | what the breaking commit changes |
| --- | --- |
| `case/dep-bump-unpinned` | loosens a `packaging` pin, so a release that dropped `LegacyVersion` gets installed |
| `case/dep-bump-conflict` | pins `httpcore` to a version `httpx` cannot use: the resolver cannot build a set |
| `case/bad-commit-logic` | a shipping threshold comparison is inverted |
| `case/bad-commit-rename` | `format_total` is renamed where it is defined, not where it is imported |
| `case/flaky-coin` | an index build drops everything on a coin flip |
| `case/flaky-timing` | a cache warm gets a clock-dependent pause, and a test asserts on elapsed time |
| `case/secret-missing` | the workflow starts requiring a repository secret that was never set |
| `case/secret-invalid` | the workflow sends a token that is not valid, and the API answers 401 |
| `case/config-python` | the workflow drops the Python version below what the code needs |
| `case/config-env` | the workflow renames the env var a step reads |
| `case/bad-commit-syntax` | a loop header loses its colon |
| `case/config-working-directory` | the install step is pointed at a directory without the manifest |

No real credential is stored in this repository. The only value ever set as a
repository secret here is a deliberately invalid token.
"""

PYPROJECT = """[project]
name = "redrun-lab"
version = "0.1.0"
description = "A tiny package that exists to produce real green-then-red CI runs."
requires-python = ">=3.9"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""

CALC = '''"""Arithmetic helpers."""

from decimal import Decimal


def total_with_tax(subtotal: Decimal, rate: float) -> Decimal:
    """Subtotal plus tax at `rate`, rounded to whole cents."""
    return (subtotal * Decimal(str(1 + rate))).quantize(Decimal("0.01"))


def split_evenly(amount: Decimal, parts: int) -> list[Decimal]:
    """Split `amount` into `parts` shares; the rounding remainder rides the first."""
    if parts < 1:
        raise ValueError("parts must be at least 1")
    share = (amount / parts).quantize(Decimal("0.01"))
    return [amount - share * (parts - 1)] + [share] * (parts - 1)
'''

FORMAT = '''"""Row formatting."""


def format_row(name: str, amount: float) -> str:
    """One `name: amount` line."""
    return f"{name}: {amount:.2f}"
'''

REPORT = '''"""Builds a small text report."""

from lab.format import format_row


def render(rows: list[tuple[str, float]]) -> str:
    return "\\n".join(format_row(name, amount) for name, amount in rows)
'''

TEST_CALC = '''from decimal import Decimal

from lab.calc import split_evenly, total_with_tax


def test_total_with_tax_adds_tax_to_the_subtotal():
    assert total_with_tax(Decimal("100.00"), 0.2) == Decimal("120.00")


def test_split_evenly_keeps_the_total():
    shares = split_evenly(Decimal("10.00"), 3)
    assert sum(shares) == Decimal("10.00")
    assert len(shares) == 3
'''

TEST_REPORT = '''from lab.report import render


def test_render_one_row():
    assert render([("widgets", 12.5)]) == "widgets: 12.50"


def test_render_joins_rows_with_newlines():
    assert render([("widgets", 1.0), ("bolts", 2.0)]) == "widgets: 1.00\\nbolts: 2.00"
'''

BASE = {
    "README.md": README,
    "pyproject.toml": PYPROJECT,
    "requirements.txt": "pytest==8.3.4\n",
    "lab/__init__.py": '"""Small, boring helpers. The interest is in the runs, not the code."""\n',
    "lab/calc.py": CALC,
    "lab/format.py": FORMAT,
    "lab/report.py": REPORT,
    "tests/test_calc.py": TEST_CALC,
    "tests/test_report.py": TEST_REPORT,
    ".github/workflows/ci.yml": CI.format(python="3.11"),
}


# --------------------------------------------------------------------- the cases

VERSIONS_OK = '''"""Version helpers, built on packaging."""

from packaging.version import LegacyVersion, parse


def is_legacy_version(spec: str) -> bool:
    """True when `spec` is not a PEP 440 version."""
    return isinstance(parse(spec), LegacyVersion)


def normalise(spec: str) -> str:
    return str(parse(spec))
'''

TEST_VERSIONS = '''from lab.versions import is_legacy_version, normalise


def test_plain_version_normalises():
    assert normalise("1.2.3") == "1.2.3"


def test_non_pep440_version_is_legacy():
    assert is_legacy_version("1.2.3-not.a.version") is True


def test_pep440_version_is_not_legacy():
    assert is_legacy_version("1.2.3") is False
'''

CLIENT = '''"""A client built on httpx."""

import httpx


def make_client() -> httpx.Client:
    return httpx.Client(timeout=5.0)
'''

TEST_CLIENT = '''from lab.client import make_client


def test_client_is_constructible():
    with make_client() as client:
        assert client is not None
'''

PRICING = '''"""Order pricing."""

from decimal import Decimal

FREE_SHIPPING_OVER = Decimal("50.00")
STANDARD_SHIPPING = Decimal("4.99")
EXPRESS_SURCHARGE = Decimal("9.99")


def order_total(subtotal: Decimal, express: bool = False) -> Decimal:
    """Subtotal plus shipping; shipping is free over the threshold."""
    shipping = Decimal("0.00") if subtotal >= FREE_SHIPPING_OVER else STANDARD_SHIPPING
    if express:
        shipping += EXPRESS_SURCHARGE
    return (subtotal + shipping).quantize(Decimal("0.01"))
'''

TEST_PRICING = '''from decimal import Decimal

from lab.pricing import order_total


def test_free_shipping_over_the_threshold():
    assert order_total(Decimal("60.00")) == Decimal("60.00")


def test_standard_shipping_under_the_threshold():
    assert order_total(Decimal("10.00")) == Decimal("14.99")


def test_express_adds_a_surcharge():
    assert order_total(Decimal("10.00"), express=True) == Decimal("24.98")
'''

SUMMARY = '''"""Totals for the dashboard."""


def format_total(amount: float) -> str:
    return f"{amount:.2f}"
'''

DASHBOARD = '''"""One-line headline built from summary totals."""

from lab.summary import format_total


def headline(totals: list[float]) -> str:
    return " | ".join(format_total(total) for total in totals)
'''

TEST_DASHBOARD = '''from lab.dashboard import headline


def test_headline_joins_totals():
    assert headline([1.0, 2.5]) == "1.00 | 2.50"


def test_headline_of_nothing_is_empty():
    assert headline([]) == ""
'''

INDEX = '''"""Index building for the search page."""


def build_index(rows: list[str]) -> dict[str, int]:
    """Map each row to its position."""
    return {row: position for position, row in enumerate(rows)}
'''

TEST_INDEX = '''from lab.index import build_index


def test_index_covers_every_row():
    """One call, so the coin flip in the module decides the run exactly once."""
    index = build_index(["alpha", "beta", "gamma"])
    assert len(index) == 3
    assert index == {"alpha": 0, "beta": 1, "gamma": 2}
'''

CACHE = '''"""A cache warm-up that is supposed to be quick."""

import time


def warm(entries: int) -> int:
    """Populate `entries` slots. Returns the number warmed."""
    time.sleep(0.001)
    return entries
'''

TEST_CACHE = '''import time

from lab.cache import warm


def test_warm_is_quick():
    started = time.perf_counter()
    assert warm(50) == 50
    elapsed = time.perf_counter() - started
    assert elapsed < 0.05, f"cache warm took {elapsed:.3f}s"
'''

HEALTH = '''"""Liveness helpers. No credentials involved."""


def is_ready(state: str) -> bool:
    return state == "ready"
'''

TEST_HEALTH = '''from lab.health import is_ready


def test_ready_state():
    assert is_ready("ready") is True


def test_other_states_are_not_ready():
    assert is_ready("starting") is False
'''

REPORTING = '''"""Reporting rows. PEP 604 unions mean this needs Python 3.10 or newer."""

from dataclasses import dataclass, field


@dataclass
class Row:
    label: str
    amount: float | None = None
    tags: list[str] = field(default_factory=list)


def describe(row: Row) -> str:
    if row.amount is None:
        return f"{row.label}: n/a"
    return f"{row.label}: {row.amount:.2f}"
'''

TEST_REPORTING = '''from lab.reporting import Row, describe


def test_describe_formats_an_amount():
    assert describe(Row("widgets", 12.5)) == "widgets: 12.50"


def test_describe_handles_a_missing_amount():
    assert describe(Row("bolts")) == "bolts: n/a"
'''

MODE = '''"""Reads the mode this run is configured for."""

import os


def app_mode() -> str:
    """The deployment mode, which the workflow supplies."""
    return os.environ["LAB_APP_MODE"]
'''

TEST_MODE = '''from lab.mode import app_mode


def test_app_mode_is_known():
    assert app_mode() in {"ci", "local"}
'''

PARSE = '''"""Parses a CSV-ish row."""


def parse_row(line: str) -> dict[str, str]:
    """Split `key=value,key=value` into a dict."""
    fields: dict[str, str] = {}
    for part in line.split(","):
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    return fields
'''

TEST_PARSE = '''from lab.parse import parse_row


def test_parse_row_reads_every_pair():
    assert parse_row("a=1, b=2") == {"a": "1", "b": "2"}


def test_parse_row_with_padding():
    assert parse_row("  key = value  ") == {"key": "value"}
'''

QUEUE = '''"""A tiny in-memory queue."""


class Queue:
    def __init__(self) -> None:
        self._items: list[str] = []

    def add(self, item: str) -> None:
        self._items.append(item)

    def drain(self) -> list[str]:
        items, self._items = self._items, []
        return items
'''

TEST_QUEUE = '''from lab.queue import Queue


def test_drain_empties_the_queue():
    queue = Queue()
    queue.add("first")
    assert queue.drain() == ["first"]
    assert queue.drain() == []
'''

CI_ENV_BLOCK = """jobs:
  test:
    runs-on: ubuntu-latest
    env:
      LAB_APP_MODE: ci
"""


def _with_job_env(python: str, env_name: str) -> str:
    """The main workflow plus a job-level env var under a chosen name."""
    text = CI.format(python=python)
    text = text.replace("jobs:\n  test:\n    runs-on: ubuntu-latest\n", CI_ENV_BLOCK)
    text = text.replace("LAB_APP_MODE: ci", f"{env_name}: ci")
    return text


CI_SECRET_STEP = """      - name: publish
        env:
          LAB_TOKEN: ${{ secrets.LAB_MISSING_TOKEN }}
        run: |
          python - <<'PY'
          import os
          import sys

          if not os.environ.get("LAB_TOKEN"):
              sys.exit("refusing to publish: required secret LAB_TOKEN is empty")
          PY
"""

CI_INVALID_TOKEN_STEP = """      - name: verify token
        env:
          LAB_TOKEN: ${{ secrets.LAB_TOKEN }}
        run: |
          code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $LAB_TOKEN" https://api.github.com/user)
          echo "github api answered $code"
          test "$code" = "200"
"""


def _with_step(python: str, step: str) -> str:
    """The main workflow with one extra step appended after the tests."""
    text = CI.format(python=python)
    marker = "      - name: test\n        run: python -m pytest -q\n"
    return text.replace(marker, marker + step)


WORKDIR_STEP = (
    "      - name: install\n"
    "        working-directory: lab\n"
    "        run: python -m pip install --upgrade pip && python -m pip install -r requirements.txt\n"
)


def _in_directory(python: str, directory: str) -> str:
    """The install step pointed at a subdirectory, where requirements.txt is not."""
    text = CI.format(python=python)
    plain = (
        "      - name: install\n"
        "        run: python -m pip install --upgrade pip && python -m pip install -r requirements.txt\n"
    )
    return text.replace(plain, WORKDIR_STEP.replace("working-directory: lab", f"working-directory: {directory}"))


CASES: list[dict] = [
    {
        "id": "dep_bump_unpinned",
        "branch": "case/dep-bump-unpinned",
        "cause": "dependency_bump",
        "green": {
            "requirements.txt": "pytest==8.3.4\npackaging==21.3\n",
            "lab/versions.py": VERSIONS_OK,
            "tests/test_versions.py": TEST_VERSIONS,
        },
        "red": {"requirements.txt": "pytest==8.3.4\npackaging>=21\n"},
        "green_msg": "add version helpers, pinned to packaging 21.3",
        "red_msg": "let packaging float to whatever the resolver picks",
        "notes": (
            "The pin is loosened, so pip installs the newest packaging, which no longer "
            "exports LegacyVersion. A removed API arriving through an unpinned floor."
        ),
    },
    {
        "id": "dep_bump_conflict",
        "branch": "case/dep-bump-conflict",
        "cause": "dependency_bump",
        "green": {
            "requirements.txt": "pytest==8.3.4\nhttpx==0.27.0\nhttpcore==1.0.7\n",
            "lab/client.py": CLIENT,
            "tests/test_client.py": TEST_CLIENT,
        },
        "red": {"requirements.txt": "pytest==8.3.4\nhttpx==0.27.0\nhttpcore==0.16.0\n"},
        "green_msg": "add an httpx client and a consistent pin set",
        "red_msg": "pin httpcore to a version httpx cannot use",
        "notes": (
            "httpx 0.27 requires httpcore 1.x, so pinning httpcore 0.16 makes the "
            "requirement set unsatisfiable. pip fails before it installs anything."
        ),
    },
    {
        "id": "bad_commit_logic",
        "branch": "case/bad-commit-logic",
        "cause": "bad_commit",
        "green": {"lab/pricing.py": PRICING, "tests/test_pricing.py": TEST_PRICING},
        "red": {
            "lab/pricing.py": PRICING.replace(
                "subtotal >= FREE_SHIPPING_OVER", "subtotal <= FREE_SHIPPING_OVER"
            )
        },
        "green_msg": "add order pricing with a free shipping threshold",
        "red_msg": "invert the free shipping comparison",
        "notes": "A wrong-value assertion: the test file is unchanged, the source is not.",
    },
    {
        "id": "bad_commit_rename",
        "branch": "case/bad-commit-rename",
        "cause": "bad_commit",
        "green": {
            "lab/summary.py": SUMMARY,
            "lab/dashboard.py": DASHBOARD,
            "tests/test_dashboard.py": TEST_DASHBOARD,
        },
        "red": {"lab/summary.py": SUMMARY.replace("format_total", "render_total")},
        "green_msg": "add dashboard totals",
        "red_msg": "rename format_total at its definition and nowhere else",
        "notes": "An import error in a changed module: the importer is the unchanged file.",
    },
    {
        "id": "flaky_coin",
        "branch": "case/flaky-coin",
        "cause": "flaky_test",
        "green": {"lab/index.py": INDEX, "tests/test_index.py": TEST_INDEX},
        "red": {
            "lab/index.py": INDEX.replace(
                '"""Index building for the search page."""\n',
                '"""Index building for the search page."""\n\nimport random\n',
            ).replace(
                "    return {row: position for position, row in enumerate(rows)}",
                "    if random.random() < 0.5:\n"
                '        # The upstream indexer drops everything when its queue is busy.\n'
                "        return {}\n"
                "    return {row: position for position, row in enumerate(rows)}",
            )
        },
        "green_msg": "add the index builder",
        "red_msg": "make the index build fail about half the time",
        "rerun": True,
        "notes": "Same SHA has to fail once and then pass on rerun: that is the flake signal.",
    },
    {
        "id": "flaky_timing",
        "branch": "case/flaky-timing",
        "cause": "flaky_test",
        "green": {"lab/cache.py": CACHE, "tests/test_cache.py": TEST_CACHE},
        "red": {
            "lab/cache.py": CACHE.replace(
                "    time.sleep(0.001)",
                "    time.sleep(0.001 + random.uniform(0.0, 0.10))",
            ).replace(
                '"""A cache warm-up that is supposed to be quick."""\n\nimport time\n',
                '"""A cache warm-up that is supposed to be quick."""\n\nimport random\nimport time\n',
            )
        },
        "green_msg": "add the cache warm-up",
        "red_msg": "let the warm-up pause for a random slice of a second",
        "rerun": True,
        "notes": "A clock-dependent pause against a 50ms budget: the test is the clock's hostage.",
    },
    {
        "id": "secret_missing",
        "branch": "case/secret-missing",
        "cause": "expired_or_missing_secret",
        "green": {"lab/health.py": HEALTH, "tests/test_health.py": TEST_HEALTH},
        "red": {".github/workflows/ci.yml": _with_step("3.11", CI_SECRET_STEP)},
        "green_msg": "add liveness helpers",
        "red_msg": "require a repository secret that was never set",
        "notes": "secrets.LAB_MISSING_TOKEN does not exist, so the step refuses to publish.",
    },
    {
        "id": "secret_invalid",
        "branch": "case/secret-invalid",
        "cause": "expired_or_missing_secret",
        "green": {"lab/health.py": HEALTH, "tests/test_health.py": TEST_HEALTH},
        "red": {".github/workflows/ci.yml": _with_step("3.11", CI_INVALID_TOKEN_STEP)},
        "green_msg": "add liveness helpers",
        "red_msg": "send a token that is not valid and check the status code",
        "needs_repo_secret": {"LAB_TOKEN": "invalid-token-redrun-lab-not-a-real-credential"},
        "notes": "LAB_TOKEN is set to a deliberately invalid value, so the API answers 401.",
    },
    {
        "id": "config_python",
        "branch": "case/config-python",
        "cause": "config_change",
        "green": {"lab/reporting.py": REPORTING, "tests/test_reporting.py": TEST_REPORTING},
        "red": {".github/workflows/ci.yml": CI.format(python="3.9")},
        "green_msg": "add reporting rows",
        "red_msg": "run the job on Python 3.9, below what the annotations need",
        "notes": "Only the workflow changes: the code needs 3.10 for PEP 604 unions.",
    },
    {
        "id": "config_env",
        "branch": "case/config-env",
        "cause": "config_change",
        "green": {
            ".github/workflows/ci.yml": _with_job_env("3.11", "LAB_APP_MODE"),
            "lab/mode.py": MODE,
            "tests/test_mode.py": TEST_MODE,
        },
        "red": {".github/workflows/ci.yml": _with_job_env("3.11", "LAB_RUN_MODE")},
        "green_msg": "add a mode reader and configure it in the workflow",
        "red_msg": "rename the env var the reader expects",
        "notes": "The var the step reads is renamed, so the reader raises KeyError.",
    },
    {
        "id": "bad_commit_syntax",
        "branch": "case/bad-commit-syntax",
        "cause": "bad_commit",
        "green": {"lab/parse.py": PARSE, "tests/test_parse.py": TEST_PARSE},
        "red": {
            "lab/parse.py": PARSE.replace(
                '    for part in line.split(","):', '    for part in line.split(",")'
            )
        },
        "green_msg": "add a row parser",
        "red_msg": "drop the colon from the loop header",
        "notes": (
            "A syntax error in a changed module: pytest cannot even import it, and the "
            "traceback names the changed file, so the log-to-change join has something "
            "to join on."
        ),
    },
    {
        "id": "config_working_directory",
        "branch": "case/config-working-directory",
        "cause": "config_change",
        "green": {"lab/queue.py": QUEUE, "tests/test_queue.py": TEST_QUEUE},
        "red": {".github/workflows/ci.yml": _in_directory("3.11", "lab")},
        "green_msg": "add a small queue",
        "red_msg": "run the install step from lab/, where requirements.txt is not",
        "notes": (
            "Only the workflow changes. The step starts, then pip cannot find the "
            "manifest it was pointed at."
        ),
    },
]

BY_ID = {case["id"]: case for case in CASES}


# ----------------------------------------------------------------------- plumbing


def token() -> str:
    value = os.environ.get("GITHUB_TOKEN")
    if value:
        return value
    for line in Path("~/.git-credentials").expanduser().read_text().splitlines():
        if "@github.com" in line:
            prefix, _, tail = line.partition("://")
            credentials, _, _ = tail.partition("@")
            _, _, secret = credentials.partition(":")
            if secret:
                return secret
    raise SystemExit("no GitHub token available")


def api(method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "redrun-lab-builder",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw.decode("utf-8", "replace")[:400]}


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # A passphrase-less deploy key is what makes the workflow file pushable at all:
    # GitHub refuses to let a PAT without the `workflow` scope create or update a
    # workflow file, over HTTPS and over the API alike. The key is added for the
    # build and removed again in `--close-out`.
    if SSH_KEY.exists():
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
        )
    result = subprocess.run(
        ["git", "-C", str(WORK), *args], capture_output=True, text=True, env=env
    )
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def write_tree(files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = WORK / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"repo": f"{OWNER}/{REPO}", "cases": {}}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def run_for(branch: str, sha: str) -> dict | None:
    """The run for one commit on one branch, or None if it has not appeared yet.

    Matching on the sha matters. A branch-wide fallback returns the previous run,
    which has already completed, so a caller that waits for the recorded run to
    finish would be handed the green run and never wait at all.
    """
    status, payload = api(
        "GET",
        f"/repos/{OWNER}/{REPO}/actions/runs?"
        + urllib.parse.urlencode({"branch": branch, "per_page": 50}),
    )
    if status != 200:
        raise SystemExit(f"listing runs failed: {status} {payload}")
    for run in payload.get("workflow_runs", []):
        if run.get("head_sha") == sha and run.get("head_branch") == branch:
            return run
    return None


def wait_for_run(branch: str, sha: str, timeout_s: int = 900) -> dict:
    """Wait for the branch's run of `sha` to reach a conclusion."""
    deadline = time.time() + timeout_s
    seen = False
    while time.time() < deadline:
        run = run_for(branch, sha)
        if run is None:
            time.sleep(5)
            continue
        seen = True
        if run.get("status") == "completed" and run.get("conclusion"):
            return run
        time.sleep(10)
    raise SystemExit(f"run for {branch}@{sha[:7]} did not finish (seen={seen})")


def ensure_repo() -> None:
    status, payload = api("GET", f"/repos/{OWNER}/{REPO}")
    if status == 200:
        print(f"repo exists: {payload['full_name']} public={not payload['private']}")
        return
    status, payload = api(
        "POST",
        "/user/repos",
        {
            "name": REPO,
            "description": "Green-then-red GitHub Actions runs, built as RedRun's evaluation lab.",
            "private": False,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        },
    )
    if status not in (200, 201):
        raise SystemExit(f"repo create failed: {status} {payload}")
    print(f"created {payload['full_name']}")


def set_repo_secret(name: str, value: str) -> None:
    from nacl import encoding, public  # imported here so the pilot run does not need it

    status, payload = api("GET", f"/repos/{OWNER}/{REPO}/actions/secrets/public-key")
    if status != 200:
        raise SystemExit(f"public key fetch failed: {status} {payload}")
    key = public.PublicKey(payload["key"].encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(key).encrypt(value.encode("utf-8"))
    status, payload = api(
        "PUT",
        f"/repos/{OWNER}/{REPO}/actions/secrets/{name}",
        {"encrypted_value": __import__("base64").b64encode(sealed).decode(), "key_id": payload["key_id"]},
    )
    if status not in (201, 204):
        raise SystemExit(f"secret {name} failed: {status} {payload}")
    print(f"set repository secret {name} to a deliberately invalid value")


def prepare_workdir() -> None:
    if (WORK / ".git").exists():
        git("remote", "set-url", "origin", REMOTE_SSH if SSH_KEY.exists() else REMOTE_HTTPS)
        git("fetch", "origin", "--prune")
        return
    WORK.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(WORK)], check=True, capture_output=True
    )
    git("remote", "add", "origin", REMOTE_SSH if SSH_KEY.exists() else REMOTE_HTTPS)
    git("config", "user.name", "cipoklean")
    git("config", "user.email", "cipoklean@users.noreply.github.com")


def commit(message: str) -> str:
    git("add", "-A")
    staged = git("diff", "--cached", "--name-only").stdout.strip()
    if staged:
        git("commit", "-q", "-m", message)
    else:
        print(f"    (nothing new to commit for {message!r})")
    return git("rev-parse", "HEAD").stdout.strip()


def push(refspec: str, *, force: bool = False) -> None:
    args = ["push", "-q", "origin", refspec]
    if force:
        # Only ever used for this throwaway lab repo, which this script alone writes to.
        args.insert(2, "--force")
    result = git(*args, check=False)
    if result.returncode != 0:
        raise SystemExit(f"push {refspec} failed: {result.stderr.strip()}")


# ------------------------------------------------------------------------- phases


def phase_main() -> None:
    ensure_repo()
    prepare_workdir()
    write_tree(BASE)
    sha = commit("a tiny package, its tests and a workflow that runs them")
    push("HEAD:main", force=True)
    state = load_state()
    state["main_sha"] = sha
    save_state(state)
    run = wait_for_run("main", sha)
    state["main_run"] = {"id": run["id"], "url": run["html_url"], "conclusion": run["conclusion"]}
    save_state(state)
    print(f"main: {sha[:7]} {run['conclusion']} {run['html_url']}")


def phase_close_out() -> None:
    """Remove the temporary deploy key once the lab has been built."""
    key_id_path = ROOT / ".cache" / "labkeys" / "key_id"
    if not key_id_path.exists():
        print("no recorded deploy key id; nothing to remove")
        return
    key_id = key_id_path.read_text().strip()
    status, payload = api("DELETE", f"/repos/{OWNER}/{REPO}/keys/{key_id}")
    if status not in (204, 404):
        raise SystemExit(f"deploy key delete failed: {status} {payload}")
    print(f"deploy key {key_id} removed ({'already gone' if status == 404 else 'deleted'})")
    key_id_path.unlink()


def phase_green() -> None:
    state = load_state()
    prepare_workdir()
    pending: list[tuple[str, str, str]] = []
    for case in CASES:
        git("checkout", "-q", "-B", case["branch"], "origin/main")
        write_tree(case["green"])
        sha = commit(case["green_msg"])
        push(f"HEAD:{case['branch']}")
        pending.append((case["id"], case["branch"], sha))
        print(f"pushed green {case['id']} {sha[:7]}")
    for case_id, branch, sha in pending:
        run = wait_for_run(branch, sha)
        entry = state["cases"].setdefault(case_id, {})
        entry.update(
            {
                "branch": branch,
                "green_sha": sha,
                "green_run_id": run["id"],
                "green_run_url": run["html_url"],
                "green_conclusion": run["conclusion"],
            }
        )
        save_state(state)
        print(f"green {case_id}: {run['conclusion']} {run['html_url']}")
    git("checkout", "-q", "main")


def phase_red() -> None:
    state = load_state()
    prepare_workdir()
    for case in CASES:
        entry = state["cases"].setdefault(case["id"], {})
        if entry.get("red_conclusion"):
            print(f"skip {case['id']}: already recorded")
            continue
        for name, value in (case.get("needs_repo_secret") or {}).items():
            set_repo_secret(name, value)
        git("checkout", "-q", case["branch"])
        write_tree(case["red"])
        sha = commit(case["red_msg"])
        push(f"HEAD:{case['branch']}")
        run = wait_for_run(case["branch"], sha)
        entry.update(
            {
                "red_sha": sha,
                "red_run_id": run["id"],
                "red_run_url": run["html_url"],
                "red_conclusion": run["conclusion"],
                "red_run_attempt": run.get("run_attempt"),
            }
        )
        if case.get("rerun"):
            entry.update(flaky_rerun(run))
        save_state(state)
        print(
            f"red {case['id']}: {entry['red_conclusion']}"
            + (f" -> rerun {entry.get('rerun_conclusion')}" if case.get("rerun") else "")
        )
    git("checkout", "-q", "main")


def flaky_rerun(run: dict, attempts: int = 8) -> dict:
    """Rerun the same SHA until an attempt passes after the failing one."""
    run_id = run["id"]
    failed_attempt = run.get("run_attempt") or 1
    if run.get("conclusion") != "failure":
        return {"rerun_conclusion": "no-failing-attempt", "rerun_attempts": 0}
    for attempt in range(1, attempts + 1):
        status, payload = api(
            "POST", f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}/rerun", {}
        )
        if status not in (201, 202, 200):
            return {"rerun_conclusion": f"rerun-refused:{status}", "rerun_attempts": attempt}
        time.sleep(15)
        latest = run
        deadline = time.time() + 600
        while time.time() < deadline:
            status, fetched = api("GET", f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}")
            if status == 200 and fetched.get("status") == "completed":
                latest = fetched
                break
            time.sleep(10)
        print(
            f"    rerun attempt {attempt}: {latest.get('conclusion')} "
            f"(run_attempt {latest.get('run_attempt')})"
        )
        if latest.get("conclusion") == "success":
            return {
                "rerun_conclusion": "success",
                "rerun_attempts": attempt,
                "failed_attempt": failed_attempt,
                "passing_attempt": latest.get("run_attempt"),
                "same_sha": latest.get("head_sha"),
            }
    return {"rerun_conclusion": "still-failing", "rerun_attempts": attempts}


def dispatch_run(branch: str, workflow_file: str = "ci.yml", timeout_s: int = 900) -> dict:
    """Start a workflow_dispatch run on `branch` and wait for that exact run.

    The ids already on the branch are collected first: a dispatched run shares its
    head_sha with the push run, and the list is newest first, so without that the
    previous dispatch would be read back as if it were the new one.
    """
    status, listing = api(
        "GET",
        f"/repos/{OWNER}/{REPO}/actions/runs?"
        + urllib.parse.urlencode(
            {"branch": branch, "event": "workflow_dispatch", "per_page": 20}
        ),
    )
    existing = (listing.get("workflow_runs") or []) if isinstance(listing, dict) else []
    status, payload = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/actions/workflows/{workflow_file}/dispatches",
        {"ref": branch},
    )
    if status not in (201, 204):
        raise SystemExit(f"dispatch on {branch} failed: {status} {payload}")

    run_id: int | None = None
    seen = {int(item["id"]) for item in existing}
    deadline = time.time() + 90
    while time.time() < deadline and run_id is None:
        status, listing = api(
            "GET",
            f"/repos/{OWNER}/{REPO}/actions/runs?"
            + urllib.parse.urlencode(
                {"branch": branch, "event": "workflow_dispatch", "per_page": 20}
            ),
        )
        runs = listing.get("workflow_runs") if isinstance(listing, dict) else None
        for item in runs or []:
            if int(item["id"]) not in seen:
                run_id = int(item["id"])
                break
        if run_id is None:
            time.sleep(5)
    if run_id is None:
        raise SystemExit(f"no new dispatched run appeared for {branch}")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, run = api("GET", f"/repos/{OWNER}/{REPO}/actions/runs/{run_id}")
        if status == 200 and run.get("status") == "completed":
            return run
        time.sleep(10)
    raise SystemExit(f"dispatched run {run_id} on {branch} did not finish")


def phase_flaky() -> None:
    """Give every flake case a run that is failed now, and a pass on the same sha.

    A rerun mutates the run in place: the rerun that passes is the same run, so it
    erases the failure the case is built on. A `workflow_dispatch` run lands on the
    same branch head as a separate run, which leaves the failed one failed and still
    shows one commit failing once and passing once.
    """
    state = load_state()
    prepare_workdir()
    for case in CASES:
        if not case.get("rerun"):
            continue
        entry = state["cases"].setdefault(case["id"], {})
        branch = case["branch"]
        git("checkout", "-q", branch)
        head = git("rev-parse", "HEAD").stdout.strip()

        failed: dict | None = None
        for nudge in range(1, 7):
            run = run_for(branch, head)
            if run is not None and run.get("status") == "completed":
                if run.get("conclusion") == "failure":
                    failed = run
                    break
                print(f"  {case['id']}: run {run['id']} passed; nudging for a fresh run")
            else:
                run = wait_for_run(branch, head)
                if run.get("conclusion") == "failure":
                    failed = run
                    break
            first_red = sorted(case["red"])[0]
            path = WORK / first_red
            path.write_text(
                path.read_text(encoding="utf-8") + f"\n# nudge {nudge}\n", encoding="utf-8"
            )
            head = commit(f"{case['red_msg']} (nudge {nudge})")
            push(f"HEAD:{branch}")
            time.sleep(5)
        if failed is None:
            print(f"  {case['id']}: no failed run after six attempts; leaving it alone")
            continue

        entry.update(
            {
                "red_sha": failed.get("head_sha"),
                "red_run_id": failed["id"],
                "red_run_url": failed["html_url"],
                "red_conclusion": failed.get("conclusion"),
                "red_run_attempt": failed.get("run_attempt"),
            }
        )
        passing = None
        for attempt in range(1, 7):
            run = dispatch_run(branch)
            print(f"  {case['id']}: dispatched run {run['id']} -> {run.get('conclusion')}")
            if run.get("conclusion") == "success":
                passing = run
                break
        if passing is None:
            print(f"  {case['id']}: no passing run on the same sha yet")
        else:
            entry.update(
                {
                    "pass_run_id": passing["id"],
                    "pass_run_url": passing["html_url"],
                    "pass_event": "workflow_dispatch",
                    "same_sha": passing.get("head_sha") == failed.get("head_sha"),
                    "failed_attempt": failed.get("run_attempt"),
                }
            )
            print(
                f"  {case['id']}: failed {failed['id']} and passed {passing['id']} "
                f"on sha {(failed.get('head_sha') or '')[:7]} "
                f"(same sha: {passing.get('head_sha') == failed.get('head_sha')})"
            )
        save_state(state)
    git("checkout", "-q", "main")


def phase_reset_red() -> None:
    """Forget the breaking-run half of the state so the red phase can be re-read."""
    state = load_state()
    for entry in state["cases"].values():
        for key in (
            "red_sha",
            "red_run_id",
            "red_run_url",
            "red_conclusion",
            "red_run_attempt",
            "rerun_conclusion",
            "rerun_attempts",
            "pass_run_id",
            "pass_run_url",
            "pass_event",
            "same_sha",
            "failed_attempt",
        ):
            entry.pop(key, None)
    save_state(state)
    print("cleared the recorded red runs; run the red phase again")


def phase_rebuild(case_id: str) -> None:
    """Recreate one branch from main, for when the lab's own content was wrong.

    Deleting and re-pushing the branch gives fresh runs. Nothing about RedRun is
    involved: this is the lab fixing itself before any recording happens.
    """
    state = load_state()
    prepare_workdir()
    case = BY_ID[case_id]
    branch = case["branch"]
    git("push", "origin", "--delete", branch, check=False)
    git("checkout", "-q", "-B", branch, "origin/main")
    write_tree(case["green"])
    green_sha = commit(case["green_msg"])
    push(f"HEAD:{branch}")
    green = wait_for_run(branch, green_sha)
    entry = {
        "branch": branch,
        "green_sha": green_sha,
        "green_run_id": green["id"],
        "green_run_url": green["html_url"],
        "green_conclusion": green["conclusion"],
    }
    print(f"green {case_id}: {green['conclusion']} {green['html_url']}")
    write_tree(case["red"])
    red_sha = commit(case["red_msg"])
    push(f"HEAD:{branch}")
    red = wait_for_run(branch, red_sha)
    entry.update(
        {
            "red_sha": red_sha,
            "red_run_id": red["id"],
            "red_run_url": red["html_url"],
            "red_conclusion": red["conclusion"],
            "red_run_attempt": red.get("run_attempt"),
        }
    )
    print(f"red {case_id}: {red['conclusion']} {red['html_url']}")
    state["cases"][case_id] = entry
    save_state(state)
    git("checkout", "-q", "main")


def phase_status() -> None:
    state = load_state()
    print(json.dumps(state, indent=2))


PHASES = {
    "main": phase_main,
    "green": phase_green,
    "red": phase_red,
    "flaky": phase_flaky,
    "reset-red": phase_reset_red,
    "status": phase_status,
    "close-out": phase_close_out,
}


def main(argv: list[str]) -> int:
    if not argv or (argv[0] not in PHASES and argv[0] != "rebuild"):
        print(f"usage: {Path(__file__).name} {{{'|'.join(PHASES)}}} [rebuild <case-id>]")
        return 2
    if argv[0] == "rebuild":
        if len(argv) < 2 or argv[1] not in BY_ID:
            print(f"rebuild needs a case id: {', '.join(sorted(BY_ID))}")
            return 2
        phase_rebuild(argv[1])
        return 0
    PHASES[argv[0]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
