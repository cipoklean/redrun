"""Assemble eval/cases.yaml from the lab's recorded runs and the chosen public cases.

    python eval/lab/make_cases.py            # write the case file
    python eval/lab/make_cases.py --print    # show what it would write

The split is not chosen here: it follows from the seed in `SEED` and from nothing
else, so re-running this after seeing results cannot move a case into dev. A test
recomputes the same draw and fails if the file disagrees.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
sys.path.insert(0, str(ROOT))

from redrun.eval.cases import choose_holdout  # noqa: E402

SEED = 20261025
OUT = ROOT / "eval" / "cases.yaml"
LAB_STATE = LAB / "lab_runs.json"
PUBLIC_STATE = LAB / "public_candidates.json"

HEADER = """# RedRun's evaluation set.
#
# The split is drawn once from `seed` with `redrun.eval.cases.choose_holdout` and
# then frozen here. `tests/test_eval_cases.py` recomputes the draw and fails if the
# holdout list changes, so a case cannot be moved out of it after its results are
# known. Re-cut it only by changing the seed deliberately, in its own commit.
#
# 15 cases: 12 constructed in cipoklean/redrun-lab (a real green run on the branch,
# then the breaking commit), 2 public runs that overlap a GitHub Actions incident,
# and 1 public run with no usable signal. The itemised brief for this phase lists
# 2 + 2 + 2 + 2 + 2 constructed and 2 + 1 public, which is 13; the two extra come
# from variants the same brief names but gives no case: a syntax error in a changed
# module, and a working-directory change.
#
# `root_cause_keywords` are words a correct explanation has to contain. They are
# matched case-insensitively against the returned root cause, all of them required.
"""

# Words an honest explanation of each constructed case must contain. Chosen from the
# artefact the failure names plus the mechanism, not from any model's phrasing.
LAB_KEYWORDS: dict[str, list[str]] = {
    "dep_bump_unpinned": ["packaging", "pin"],
    "dep_bump_conflict": ["httpcore", "httpx"],
    "bad_commit_logic": ["shipping", "order_total"],
    "bad_commit_rename": ["format_total", "summary"],
    "bad_commit_syntax": ["parse", "syntax"],
    "flaky_coin": ["index", "random"],
    "flaky_timing": ["cache", "sleep"],
    "secret_missing": ["secret", "token"],
    "secret_invalid": ["401", "token"],
    "config_python": ["python", "3.9"],
    "config_env": ["env", "mode"],
    "config_working_directory": ["working", "requirements"],
}

LAB_NOTES_EXTRA: dict[str, str] = {
    "flaky_coin": "The failing run and the passing run are two runs of the same sha.",
    "flaky_timing": "The failing run and the passing run are two runs of the same sha.",
}


def lab_cases() -> list[dict]:
    state = json.loads(LAB_STATE.read_text(encoding="utf-8"))
    sys.path.insert(0, str(LAB))
    import build_lab  # noqa: PLC0415 - the case definitions live with the run builder

    out: list[dict] = []
    for case_id, entry in state["cases"].items():
        definition = build_lab.BY_ID.get(case_id)
        if definition is None:
            print(f"  skipping {case_id}: not in build_lab.CASES")
            continue
        if entry.get("red_conclusion") != "failure":
            print(f"  skipping {case_id}: no failed run recorded yet")
            continue
        note = (definition.get("notes") or "").strip()
        extra = LAB_NOTES_EXTRA.get(case_id)
        if extra:
            note = f"{note} {extra}"
        if entry.get("same_sha"):
            note += (
                f" Failed run {entry['red_run_url']}, passing run on the same sha "
                f"{entry['pass_run_url']} ({entry.get('pass_event')})."
            )
        out.append(
            {
                "id": case_id,
                "url": entry["red_run_url"],
                "expected_cause": definition["cause"],
                "root_cause_keywords": LAB_KEYWORDS[case_id],
                "constructed": True,
                "notes": note.strip(),
            }
        )
    out.sort(key=lambda item: item["id"])
    return out


# Hand-picked public cases, written out with the evidence each one was chosen on.
#
# Note on "the same SHA later passed on rerun": GitHub reruns a run in place, so a
# rerun that passes overwrites the failure this case needs as its URL. That is why
# the flake cases in the lab use a workflow_dispatch run for the passing half, and
# why these two are justified by the same commit passing in other workflows
# instead. The weaker evidence is recorded here rather than dressed up.
PUBLIC: list[dict] = [
    {
        "id": "infra_outage_pandas_action_download",
        "url": "https://github.com/pandas-dev/pandas/actions/runs/32042902095",
        "expected_cause": "infra_outage",
        "root_cause_keywords": ["download", "github"],
        "constructed": False,
        "notes": (
            "Chosen for the 2026-08-17 critical incident 'Incident with GitHub.com' "
            "(13:40-21:15Z), inside which this run failed at 15:38Z. Its log says "
            "`##[error]Failed to download archive "
            "'https://codeload.github.com/prefix-dev/setup-pixi/zip/...' after 3 attempts`, "
            "so the runner could not fetch an action from GitHub's own archive host. "
            "Three other workflows on the same sha (4444662) succeeded, so the commit is "
            "not what broke."
        ),
    },
    {
        "id": "infra_outage_django_connection_reset",
        "url": "https://github.com/django/django/actions/runs/39651790910",
        "expected_cause": "infra_outage",
        "root_cause_keywords": ["connection", "reset"],
        "constructed": False,
        "notes": (
            "Chosen for the 2026-10-21 critical incident 'Delays for Pull Requests and "
            "Webhook deliveries' (14:52-19:22Z), inside which this run failed at 18:26Z. "
            "The tests died on `ConnectionResetError(104, 'Connection reset by peer')`, and "
            "'Linters' on the same sha (3e2fa64) succeeded. Weaker than the pandas case: a "
            "reset inside a test process could be the test's own remote call, so this one "
            "is a network failure during an incident rather than a proven runner fault."
        ),
    },
]


PUBLIC_UNKNOWN: list[dict] = [
    {
        "id": "unknown_django_cancelled",
        "url": "https://github.com/django/django/actions/runs/40183021230",
        "expected_cause": "unknown",
        "root_cause_keywords": ["cancel", "cause"],
        "constructed": False,
        "notes": (
            "A cancelled run with nothing to read: its only job was cancelled, the whole "
            "log is 253 lines that end at 'Cleaning up orphan processes', and none of the "
            "collector's error patterns match a line of it. The workflow had seven earlier "
            "successful runs on the same branch, so a green baseline exists and the "
            "comparison still yields no signal. `unknown` is the correct answer here."
        ),
    }
]


def assign_split(cases: list[dict]) -> None:
    holdout = set(choose_holdout_lite(cases))
    for case in cases:
        case["split"] = "holdout" if case["id"] in holdout else "dev"


def choose_holdout_lite(cases: list[dict]) -> tuple[str, ...]:
    """`choose_holdout` over plain dicts, so this script and the loader agree."""
    from redrun.eval.cases import Case
    from redrun.core.taxonomy import Cause

    objects = tuple(
        Case(
            id=item["id"],
            url=item["url"],
            expected_cause=Cause(item["expected_cause"]),
            root_cause_keywords=tuple(item["root_cause_keywords"]),
            split="dev",
            constructed=bool(item["constructed"]),
            notes=item["notes"],
        )
        for item in cases
    )
    return choose_holdout(objects, SEED)


def ordered(cases: list[dict]) -> list[dict]:
    keys = ("id", "url", "expected_cause", "root_cause_keywords", "split", "constructed", "notes")
    return [{key: case[key] for key in keys} for case in cases]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", dest="dry_run")
    args = parser.parse_args(argv)

    cases = lab_cases() + [dict(item) for item in PUBLIC] + [dict(item) for item in PUBLIC_UNKNOWN]
    if not cases:
        print("no cases assembled")
        return 1
    assign_split(cases)
    cases.sort(key=lambda item: item["id"])
    document = {
        "seed": SEED,
        "notes": (
            "12 constructed cases with real runs in cipoklean/redrun-lab, 2 public runs "
            "that line up with a GitHub Actions incident, and 1 public run with no "
            "usable signal. The itemised brief sums to 13; the extra two are the "
            "syntax-error and working-directory variants it names without a case."
        ),
        "cases": ordered(cases),
    }
    body = HEADER + "\n" + yaml.safe_dump(document, sort_keys=False, width=100, allow_unicode=True)
    if args.dry_run:
        print(body)
        return 0
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT} with {len(cases)} cases")
    for case in cases:
        print(f"  {case['split']:<8} {case['expected_cause']:<26} {case['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
