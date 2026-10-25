"""Run the evaluation: replay the recorded cases or re-run them live, score both
systems, and write `eval/results/<timestamp>.json` plus `eval/RESULTS.md`.

Reading the results tells you what the pipeline does. It never changes what the
pipeline does: this module only calls into the pipeline and reports.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from redrun.adapters.llm import LLMError
from redrun.collectors.base import CollectorError
from redrun.config import ROOT, Settings
from redrun.core.models import Verdict
from redrun.core.rules import rule_verdict
from redrun.eval import baseline as baseline_module
from redrun.eval import metrics, report
from redrun.eval.cases import Case, CaseSet
from redrun.pipeline import investigate, prepare, run_overlap

RESULTS_DIR = ROOT / "eval" / "results"
MARKDOWN_PATH = ROOT / "eval" / "RESULTS.md"

PIPELINE = "pipeline"
RULES_ONLY = "rules_pending_llm"
NOT_RUN = "not_run"
MODES = ("replay", "live")


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _verdict_observation(verdict: Verdict | None, evidence_ids: set[str]) -> dict[str, Any]:
    return {
        "cause": verdict.cause.value if verdict else None,
        "root_cause": verdict.root_cause if verdict else "",
        "next_step": verdict.next_step if verdict else "",
        "confidence": verdict.confidence if verdict else None,
        "citation_ok": metrics.citation_ok(verdict, evidence_ids),
    }


def _rules_only(case: Case, settings: Settings) -> tuple[Verdict, set[str], str, float]:
    """The deterministic path, used only when no LLM step was recorded for a case.

    It is reported as `rules_pending_llm`, never as the agent: the agent is the
    Markovian investigator plus the skeptic, and a rules classification is a
    different system wearing the same taxonomy.
    """
    started = time.perf_counter()
    prepared = prepare(None, case.sample, settings)
    links, _label, _skipped = run_overlap(prepared, settings, None)
    verdict = rule_verdict(
        prepared.evidence, prepared.runs.failed_run, prepared.runs.last_green_run, links
    )
    return (
        verdict,
        {item.id for item in prepared.evidence},
        prepared.trace_id,
        (time.perf_counter() - started) * 1000,
    )


def observe_agent(case: Case, settings: Settings, mode: str) -> dict[str, Any]:
    """One case through the agent, or an honest account of why it could not run."""
    if mode == "replay":
        replayed = replace(settings, sample_mode=True)
        try:
            result = investigate(None, sample=case.sample, settings=replayed)
        except CollectorError as exc:
            return {
                "path": NOT_RUN,
                "path_reason": f"no recorded sample for this case ({exc})",
                "agent": None,
                "diagnostic": {},
                "trace_id": "",
                "seconds": None,
            }
        except LLMError as exc:
            try:
                verdict, evidence_ids, trace_id, elapsed = _rules_only(case, replayed)
            except CollectorError as inner:
                return {
                    "path": NOT_RUN,
                    "path_reason": f"no recorded sample for this case ({inner})",
                    "agent": None,
                    "diagnostic": {},
                    "trace_id": "",
                    "seconds": None,
                }
            return {
                "path": RULES_ONLY,
                "path_reason": (
                    "no recorded LLM step for this case, so the agent pipeline could not "
                    f"replay it ({exc})"
                ),
                "agent": None,
                "diagnostic": {"rules_cause": verdict.cause.value, "rules_confidence": verdict.confidence},
                "trace_id": trace_id,
                "seconds": round(elapsed / 1000, 2),
            }
    else:
        result = investigate(case.url, settings=replace(settings, sample_mode=False))

    verdict = result.verdict
    evidence_ids = {item.id for item in result.evidence}
    markovian = result.metrics.markovian
    trace_path = Path(settings.logs_dir) / f"{result.trace_id}.jsonl"
    agent = _verdict_observation(verdict, evidence_ids)
    # In replay the wall clock measures the replay, not the investigation, so report
    # the recorded model latency when there is one: otherwise the agent's "latency"
    # is a few milliseconds of file reading next to the baseline's real seconds.
    recorded_latency = sum(step.latency_ms for step in markovian.per_step) if markovian else 0
    agent.update(
        {
            "guard_intervened": metrics.guard_intervened(trace_path),
            "latency_ms": recorded_latency or result.metrics.latency_ms,
            "replay_ms": result.metrics.latency_ms,
            "tokens_in": result.metrics.tokens_in,
            "tokens_out": result.metrics.tokens_out,
            "steps": markovian.steps if markovian else 0,
            "stop_reason": markovian.stop_reason if markovian else None,
            "models": sorted({step.model for step in markovian.per_step if step.model})
            if markovian
            else [],
            "engine": result.metrics.query_engine,
            "evidence_items": len(result.evidence),
            "links": len(result.links),
            "skeptic": result.skeptic.final_status if result.skeptic else None,
        }
    )
    return {
        "path": PIPELINE,
        "path_reason": "",
        "agent": agent,
        "diagnostic": {},
        "trace_id": result.trace_id,
        "seconds": None,
    }


def observe_baseline(case: Case, settings: Settings, mode: str) -> tuple[dict | None, str]:
    """The recorded baseline, or the reason there is none. Never invents a reply."""
    sample_directory = Path(settings.samples_dir) / case.sample
    if mode == "replay":
        payload = baseline_module.load(sample_directory)
        if payload is None:
            return None, f"no {baseline_module.BASELINE_FILE} recorded in {sample_directory.name}"
        return baseline_module.observation(payload), ""
    live = replace(settings, sample_mode=False)
    prepared = prepare(case.url, None, live)
    try:
        log_tail = baseline_module.tail_from_prepared(prepared)
        payload = baseline_module.record(live, sample_directory, log_tail)
    except baseline_module.BaselineError as exc:
        return None, str(exc)
    finally:
        if prepared.client is not None:
            prepared.client.close()
    return baseline_module.observation(payload), ""


def score(row: dict[str, Any], case: Case) -> dict[str, Any]:
    """Fold one case's raw observations into the numbers the report prints."""
    expected = case.expected_cause.value
    for system in ("agent", "baseline"):
        observation = row.get(system)
        if observation is None:
            continue
        ok, missing = metrics.keywords_ok(observation.get("root_cause"), case.root_cause_keywords)
        observation["category_ok"] = metrics.category_ok(observation.get("cause"), expected)
        observation["keywords_ok"] = ok
        observation["keywords_missing"] = missing
    return row


def run(
    case_set: CaseSet,
    *,
    split: str,
    mode: str,
    settings: Settings,
    with_baseline: bool,
    out_dir: Path | None = None,
    markdown_path: Path | None = None,
    on_case: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    selected = case_set.selected(split)
    say = on_case or (lambda message: None)

    rows: list[dict[str, Any]] = []
    for case in selected:
        started = time.perf_counter()
        observation = observe_agent(case, settings, mode)
        row: dict[str, Any] = {
            "id": case.id,
            "url": case.url,
            "expected_cause": case.expected_cause.value,
            "root_cause_keywords": list(case.root_cause_keywords),
            "split": case.split,
            "constructed": case.constructed,
            "notes": case.notes,
            "sample": case.sample,
            **observation,
        }
        baseline_note = ""
        if with_baseline:
            baseline_observation, baseline_note = observe_baseline(case, settings, mode)
            row["baseline"] = baseline_observation
            row["baseline_reason"] = baseline_note
        else:
            row["baseline"] = None
            row["baseline_reason"] = "not requested"
        score(row, case)
        row["seconds"] = round(row.get("seconds") or (time.perf_counter() - started), 2)
        rows.append(row)
        agent = row.get("agent")
        say(
            f"  {case.id:<26} expected={case.expected_cause.value:<26} "
            f"agent={agent['cause'] if agent else 'pending':<26} "
            f"baseline={(row['baseline'] or {}).get('cause') or 'none'}"
        )

    summaries = {}
    for name in ("dev", "holdout", "all"):
        split_rows = rows if name == "all" else [row for row in rows if row["split"] == name]
        agent_rows = [
            {"id": row["id"], "expected_cause": row["expected_cause"], **row["agent"]}
            for row in split_rows
            if row["agent"]
        ]
        baseline_rows = [
            {"id": row["id"], "expected_cause": row["expected_cause"], **row["baseline"]}
            for row in split_rows
            if row["baseline"]
        ]
        summaries[name] = {
            "agent": metrics.summarise(agent_rows, system="agent"),
            "baseline": metrics.summarise(baseline_rows, system="baseline"),
        }

    pending = metrics.pending(rows)
    for item in pending:
        row = next(entry for entry in rows if entry["id"] == item["id"])
        item["rules_cause"] = (row.get("diagnostic") or {}).get("rules_cause")

    baseline_pending = [
        {"id": row["id"], "split": row["split"], "reason": row["baseline_reason"]}
        for row in rows
        if row.get("baseline") is None
        and row.get("baseline_reason") not in ("", "not requested")
    ]

    baseline_model = next(
        (row["baseline"]["model"] for row in rows if row.get("baseline")), None
    )
    agent_models = sorted(
        {
            model
            for row in rows
            if row.get("agent")
            for model in (row["agent"].get("models") or [])
        }
    )
    # What the agent computed on. In replay that is whatever the recording says
    # answered, or the replay client's lane label when the sample predates the
    # recorder keeping the model. Named separately from the baseline so nobody reads
    # `sample/deep` as a model id.
    agent_lane_labels = sorted(
        {
            label
            for page in (row.get("agent") or {} for row in rows)
            for label in (page.get("models") or [])
            if label.startswith("sample/")
        }
    )
    payload: dict[str, Any] = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": mode,
            "split": split,
            "seed": case_set.seed,
            "case_count": len(selected),
            "cases_path": str(
                Path(settings.samples_dir).parent / "eval" / "cases.yaml"
            ),
            "baseline_lines": baseline_module.BASELINE_LINES,
            "baseline_model": baseline_model,
            "agent_models": agent_models,
            "agent_lane_labels": agent_lane_labels,
            "agent_replayed": mode == "replay",
            "baseline_replayed": mode == "replay",
            "git_sha": _git_sha(),
            "notes": case_set.notes,
        },
        "rows": rows,
        "summaries": summaries,
        "pending": pending,
        "baseline_pending": baseline_pending,
    }
    write_results(payload, out_dir=out_dir, markdown_path=markdown_path)
    return payload


def write_results(
    payload: dict[str, Any], *, out_dir: Path | None = None, markdown_path: Path | None = None
) -> tuple[Path, Path]:
    """Write the JSON payload and the rendered report. Both are generated files."""
    stamp = payload["meta"]["timestamp"].replace("-", "").replace(":", "")
    directory = Path(out_dir) if out_dir else RESULTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    rendered = report.render(payload)
    md_path = Path(markdown_path) if markdown_path else MARKDOWN_PATH
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(rendered, encoding="utf-8")
    (directory / f"{stamp}.md").write_text(rendered, encoding="utf-8")
    return json_path, md_path
