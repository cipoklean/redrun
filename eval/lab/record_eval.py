"""Record every case in the set so the whole eval replays offline.

    python eval/lab/record_eval.py [--only id,id] [--force]

This is the documented path — `python -m redrun record-sample <url> --name eval_<id>`
— driven over the case file, so the sample names cannot drift from the case ids.
Needs live credentials: GITHUB_TOKEN, a model provider, and SAMPLE_MODE=false.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from redrun.cli import record_sample  # noqa: E402
from redrun.config import load_settings  # noqa: E402
from redrun.eval import cases as cases_module  # noqa: E402


def write_meta(sample_directory: Path, case) -> None:
    """Give the sample a card on the investigate screen, sorted after the four demos.

    The eval cases are real and replayable, so they belong in the sample list; they
    must not push the curated demo cases down the page though.
    """
    meta = {
        "order": 500,
        "title": f"eval: {case.id.replace('_', ' ')}",
        "category": case.expected_cause.value,
        "description": (case.notes or "").strip()[:200] or "An evaluation case.",
    }
    (sample_directory / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )


def lab_targets() -> list[tuple[Any, str, list[str]]]:
    """(case-like, url, keywords) drawn straight from the lab state, no case file needed.

    The case file cannot be frozen until the public cases are chosen, and the holdout
    draw depends on the whole set. The lab's own runs are already final, so their
    recordings can start first.
    """
    state = json.loads((LAB / "lab_runs.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(LAB))
    import build_lab  # noqa: PLC0415 - only needed for the case definitions

    out: list[tuple[Any, str, list[str]]] = []
    for case_id, entry in sorted(state["cases"].items()):
        definition = build_lab.BY_ID.get(case_id)
        if definition is None:
            print(f"  {case_id}: not in build_lab.CASES; skipping")
            continue
        if entry.get("red_conclusion") != "failure" or not entry.get("red_run_url"):
            print(f"  {case_id}: no failed run recorded; skipping")
            continue
        case = SimpleNamespace(
            id=case_id,
            sample=f"eval_{case_id}",
            expected_cause=SimpleNamespace(value=definition["cause"]),
            notes=(definition.get("notes") or "").strip(),
        )
        out.append((case, entry["red_run_url"], []))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--force", action="store_true", help="re-record cases that exist")
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="all")
    parser.add_argument(
        "--from-lab",
        action="store_true",
        help="record the constructed cases from eval/lab/lab_runs.json, before cases.yaml exists",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    if settings.sample_mode:
        print("SAMPLE_MODE=false is required to record")
        return 2
    if not settings.github_token:
        print("GITHUB_TOKEN is required to record")
        return 2

    wanted = [item.strip() for item in (args.only or "").split(",") if item.strip()]
    if args.from_lab:
        selected = [(case, url) for case, url, _ in lab_targets() if not wanted or case.id in wanted]
    else:
        case_set = cases_module.load()
        selected = [
            (case, case.url)
            for case in case_set.selected(args.split)
            if not wanted or case.id in wanted
        ]

    started = time.time()
    failures: list[str] = []
    for index, (case, url) in enumerate(selected, start=1):
        sample_directory = Path(settings.samples_dir) / case.sample
        if sample_directory.exists() and not args.force:
            had_llm = not (sample_directory / "llm" / "pending.json").exists()
            print(
                f"[{index}/{len(selected)}] skip {case.id}: {sample_directory.name} already "
                f"recorded (llm {'recorded' if had_llm else 'pending'})"
            )
            write_meta(sample_directory, case)
            continue
        print(f"[{index}/{len(selected)}] recording {case.id}")
        print(f"    {url}")
        code = record_sample(url, case.sample, settings, with_baseline=True)
        if code == 0:
            write_meta(sample_directory, case)
        else:
            failures.append(f"{case.id} (exit {code})")
        print(f"    exit {code}", flush=True)

    print(f"\n{len(selected) - len(failures)}/{len(selected)} recorded in {time.time() - started:.0f}s")
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
