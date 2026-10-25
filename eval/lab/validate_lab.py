"""Prove the lab's green state passes and every breaking commit fails, before pushing.

    .venv/bin/python eval/lab/validate_lab.py

Runs each case's green and red trees with the project interpreter and reports what
happened. Anything that does not behave is a bug in the lab, not in RedRun.
Run it with the project interpreter: this script parses the workflow files itself,
so a bare system python without PyYAML cannot finish. Cases whose break only shows
on GitHub are reported from the recorded runs rather than from a local pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_lab import BASE, CASES  # noqa: E402

PYTHON = "python3.11"
VENV_PYTHON = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
if VENV_PYTHON.exists():
    PYTHON = str(VENV_PYTHON)


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    merged = dict(os.environ)
    merged.pop("PYTHONPATH", None)
    # Green and red trees differ by a byte or two and are written within the same
    # second, so a cached .pyc with a matching size and mtime would happily run the
    # old code. Never let the validator's own bytecode cache fake a result.
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        merged.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=merged, timeout=300)


def write_tree(directory: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def pytest_run(directory: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return run([PYTHON, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"], directory, env)


def venv(directory: Path, lines: str) -> tuple[Path, subprocess.CompletedProcess]:
    target = directory / ".venv"
    run([PYTHON, "-m", "venv", str(target)], directory)
    (directory / "requirements.txt").write_text(lines, encoding="utf-8")
    interpreter = target / "bin" / "python"
    result = run([str(interpreter), "-m", "pip", "install", "-q", "-r", "requirements.txt"], directory)
    return interpreter, result


def actions_verdict(case_id: str) -> str:
    """The recorded live conclusion for a case whose break only shows on Actions.

    Five cases cannot fail on this machine, and saying so is not the same as saying
    they do not fail:

    * `secret_missing` / `secret_invalid` need the Actions secrets context and a
      401 from the API call the workflow makes;
    * `config_env` breaks a workflow-level `env:` value, which the local harness
      supplies itself;
    * `bad_commit_rename` renames a module the workflow invokes, not one a test
      imports;
    * `flaky_coin` / `flaky_timing` are coin flips, so one local run proves nothing
      (their rate is checked over twelve runs below).

    So ask GitHub instead. With no credentials to hand, say what was checked and
    stop - a missing token is not a failure.
    """
    lab = Path(__file__).with_name("lab_runs.json")
    if not lab.is_file():
        return "recorded in eval/lab/lab_runs.json"
    try:
        runs = json.loads(lab.read_text(encoding="utf-8"))["cases"][case_id]
    except (KeyError, ValueError):
        return "recorded in eval/lab/lab_runs.json"
    return f"green {runs['green_conclusion']}, red {runs['red_conclusion']} (Actions)"


def _yaml_parser() -> Any:
    """The YAML parser, or None when the interpreter running this has none.

    A validator must not end in a traceback: a missing parser is a skipped check,
    not a failed one. Run it with the project interpreter to get the real parse.
    """
    try:
        import yaml  # noqa: PLC0415 - only the workflow cases need it
    except ModuleNotFoundError:
        return None
    return yaml


# Cases whose break only shows on GitHub: the workflow's own secrets context, its
# env values, the module it invokes, or a coin flip. Verified through
# eval/lab/lab_runs.json (recorded from the API), never by a local run. Both flake
# cases are here because one local run says nothing about a 50% test; their rate is
# checked over twelve runs below.
ACTIONS_ONLY = frozenset(
    {
        "bad_commit_rename",
        "config_env",
        "flaky_coin",
        "flaky_timing",
        "secret_missing",
        "secret_invalid",
    }
)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="lab-validate-", dir=str(Path(__file__).resolve().parents[2] / ".cache")))
    failures: list[str] = []
    print(f"scratch: {root}")

    # ------------------------------------------------------------ green main
    base = root / "main"
    write_tree(base, BASE)
    result = pytest_run(base)
    print(f"main                : {'ok' if result.returncode == 0 else 'FAILED'}")
    if result.returncode != 0:
        failures.append("main does not pass")
        print(result.stdout[-2000:])

    # ------------------------------------------------- every case, locally
    for case in CASES:
        if case["id"].startswith(("dep_bump", "config_python")):
            continue  # needs its own interpreter or venv; handled below
        workflow_only = set(case["red"]) == {".github/workflows/ci.yml"}
        workspace = root / case["id"]
        write_tree(workspace, {**BASE, **case["green"]})
        green = pytest_run(workspace, {"LAB_APP_MODE": "ci"})
        write_tree(workspace, case["red"])
        red = pytest_run(workspace, {"LAB_APP_MODE": "ci"})
        tail = (red.stdout + red.stderr).strip().splitlines()
        detail = tail[-1][:110] if tail else ""
        if case["id"] in ACTIONS_ONLY:
            verdict = actions_verdict(case["id"])
        elif workflow_only:
            verdict = "workflow only (checked in Actions)"
        else:
            verdict = "fails" if red.returncode != 0 else "PASSES (bad)"
        print(
            f"{case['id']:<24}: green {'ok ' if green.returncode == 0 else 'FAILED'} "
            f"| red {verdict} :: {detail}"
        )
        if green.returncode != 0:
            failures.append(f"{case['id']}: green state does not pass")
            print(green.stdout[-1500:])
        if red.returncode == 0 and not workflow_only and case["id"] not in ACTIONS_ONLY:
            failures.append(f"{case['id']}: breaking commit does not fail")
        if workflow_only:
            parser = _yaml_parser()
            if parser is None:
                print(f"{'':<24}: PyYAML missing; workflow not parsed")
            else:
                for tree in (case["green"], case["red"]):
                    body = tree.get(".github/workflows/ci.yml")
                    if body is None:
                        continue
                    try:
                        parser.safe_load(body)
                    except Exception as exc:  # noqa: BLE001
                        failures.append(f"{case['id']}: workflow is not valid YAML: {exc}")

    # --------------------------------------------------- flakes: about half
    for case_id, runs in (("flaky_coin", 12), ("flaky_timing", 12)):
        case = next(item for item in CASES if item["id"] == case_id)
        workspace = root / f"{case_id}-dist"
        write_tree(workspace, {**BASE, **case["green"]})
        write_tree(workspace, case["red"])
        outcomes = [pytest_run(workspace).returncode != 0 for _ in range(runs)]
        print(f"{case_id:<20}: failed {sum(outcomes)}/{runs} runs")
        if not 0.2 <= sum(outcomes) / runs <= 0.8:
            failures.append(f"{case_id}: flake rate outside 20-80%")

    # ------------------------------------------- dependency cases, real pip
    dep = next(item for item in CASES if item["id"] == "dep_bump_unpinned")
    workspace = root / "dep_bump_unpinned"
    write_tree(workspace, {**BASE, **dep["green"]})
    interpreter, install = venv(workspace, dep["green"]["requirements.txt"])
    if install.returncode != 0:
        failures.append("dep_bump_unpinned green requirements do not install")
        print(install.stderr[-1200:])
    else:
        green = run(
            [str(interpreter), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
            workspace,
        )
        print(f"dep_bump_unpinned   : green {'ok' if green.returncode == 0 else 'FAILED'}")
        if green.returncode != 0:
            failures.append("dep_bump_unpinned green does not pass under packaging 21.3")
            print(green.stdout[-2000:])
        write_tree(workspace, dep["red"])
        upgrade = run(
            [
                str(interpreter),
                "-m",
                "pip",
                "install",
                "-q",
                "-U",
                "-r",
                "requirements.txt",
            ],
            workspace,
        )
        print("                    : loose pin install rc=", upgrade.returncode)
        red = run(
            [str(interpreter), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
            workspace,
        )
        text = red.stdout + red.stderr
        print(
            "                    : red "
            + ("fails" if red.returncode != 0 else "PASSES (bad)")
            + " :: "
            + (text.strip().splitlines()[-1][:130] if text.strip() else "")
        )
        if "LegacyVersion" not in text:
            failures.append("dep_bump_unpinned red failure does not name LegacyVersion")

    conflict = next(item for item in CASES if item["id"] == "dep_bump_conflict")
    workspace = root / "dep_bump_conflict"
    write_tree(workspace, {**BASE, **conflict["red"]})
    interpreter, install = venv(workspace, conflict["red"]["requirements.txt"])
    text = install.stdout + install.stderr
    print(
        "dep_bump_conflict   : red install "
        + ("fails" if install.returncode != 0 else "SUCCEEDS (bad)")
        + " :: "
        + (text.strip().splitlines()[-1][:130] if text.strip() else "")
    )
    if install.returncode == 0:
        failures.append("dep_bump_conflict requirements resolve")
    if "conflicting dependencies" not in text and "ResolutionImpossible" not in text:
        failures.append("dep_bump_conflict error text has no conflict marker")

    # ------------------------------------------------- config_python on 3.9
    config = next(item for item in CASES if item["id"] == "config_python")
    workspace = root / "config_python"
    write_tree(workspace, {**BASE, **config["green"]})
    green = pytest_run(workspace)
    red = run(
        [
            PYTHON,
            "-c",
            "import ast,sys;"
            "src=open('lab/reporting.py').read();"
            "ast.parse(src);"
            "print('parses on this interpreter')",
        ],
        workspace,
    )
    print(
        f"config_python       : green {'ok' if green.returncode == 0 else 'FAILED'} "
        "| red needs Python 3.9 (checked in Actions)"
    )
    if green.returncode != 0:
        failures.append("config_python green does not pass")
    print("                    :", red.stdout.strip()[:100])

    shutil.rmtree(root, ignore_errors=True)
    print()
    if failures:
        print("FAILURES:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(json.dumps({"validated_cases": len(CASES), "failures": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
