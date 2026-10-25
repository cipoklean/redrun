"""Recording a sample: the writer is pure, the live orchestration lives in the CLI."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Link
from redrun.core.redact import redact
from redrun.core.tables import build_tables
from redrun.pipeline import Prepared


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_llm_pending(directory: Path, reason: str, *, captured: int = 0) -> Path:
    """Mark the model half of a sample as unrecorded.

    A partially recorded conversation cannot be replayed: the investigator's steps
    depend on each other, so half of one is a different investigation. Nothing is
    written to steps.json, and the reason is kept where a human will find it.
    """
    path = Path(directory) / "llm" / "pending.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "recorded_at": _stamp(),
                "reason": reason,
                "replayable": False,
                "steps_captured_before_failure": captured,
                "note": (
                    "The collectors, the evidence tables and the skeptic tool checks in "
                    "this sample are real. The investigator's model replies are missing, "
                    "so nothing here replays as an investigation. Record them with "
                    "`python -m redrun record-sample <url> --name <name>` once a model is "
                    "reachable, and never author them by hand."
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_baseline_pending(directory: Path, reason: str) -> Path:
    """The baseline could not be recorded either. Say so in the sample folder."""
    path = Path(directory) / "baseline_pending.json"
    path.write_text(
        json.dumps(
            {
                "recorded_at": _stamp(),
                "reason": reason,
                "note": "No baseline reply was recorded, and none has been invented.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _run_payload(runs) -> dict[str, Any]:
    workflow = {
        "id": 0,
        "name": runs.failed_run.workflow_name,
        "path": runs.failed_run.workflow_path,
    }

    def one(run) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "head_sha": run.head_sha,
            "created_at": run.created_at.isoformat(),
            "conclusion": run.conclusion,
            "jobs": [
                {
                    "job_id": job.job_id,
                    "name": job.name,
                    "conclusion": job.conclusion,
                    "steps": [
                        {"number": step.number, "name": step.name, "conclusion": step.conclusion}
                        for step in job.steps
                    ],
                }
                for job in run.jobs
            ],
        }

    return {
        "repository": {"owner": runs.owner, "repo": runs.repo},
        "workflow": workflow,
        "branch": runs.failed_run.branch,
        "runner_image": runs.failed_run.runner_image,
        "failed_run": one(runs.failed_run),
        "last_green_run": one(runs.last_green_run) if runs.last_green_run else None,
    }


def _diff_payload(changes) -> dict[str, Any]:
    patches = {
        item.metadata.get("filename"): item.excerpt
        for item in changes.evidence
        if item.kind == "file"
    }
    return {
        "commits": [
            {
                "sha": commit.sha,
                "message": commit.message,
                "author": commit.author,
                "date": commit.date.isoformat() if commit.date else None,
            }
            for commit in changes.commits
        ],
        "files": [
            {
                "filename": change.filename,
                "status": change.status,
                "additions": change.additions,
                "deletions": change.deletions,
                "patch": patches.get(change.filename, ""),
            }
            for change in changes.files
        ],
    }


def _logs_payload(prepared: Prepared) -> dict[str, Any]:
    """Raw logs when a client can re-fetch them, otherwise the recorded tail."""
    jobs = [job for job in prepared.runs.failed_run.jobs if job.conclusion != "success"]
    tails = {
        item.metadata.get("job_id"): item.excerpt
        for item in prepared.job_logs.evidence
        if item.metadata.get("tail")
    }
    payload: list[dict[str, Any]] = []
    for job in jobs or prepared.runs.failed_run.jobs:
        text = ""
        if prepared.client is not None:
            try:
                text = prepared.client.download_logs(
                    prepared.runs.owner, prepared.runs.repo, job.job_id
                )
            except Exception:  # noqa: BLE001 - fall back to the tail
                text = ""
        if not text:
            text = tails.get(job.job_id, "")
        payload.append(
            {
                "job_id": job.job_id,
                "name": job.name,
                "log": redact(text),
                "source": "live" if prepared.client is not None else "tail",
            }
        )
    return {"jobs": payload}


def _pypi_payload(prepared: Prepared, settings: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cache = Path(settings.data_dir).parent / ".cache" / "pypi"
    for record in prepared.deps.dependencies:
        cached = cache / f"{record.package}.json"
        if cached.exists():
            try:
                out[record.package] = json.loads(cached.read_text(encoding="utf-8"))
            except ValueError:
                continue
    return out


def _skeptic_payloads(prepared: Prepared, blocker: Any) -> dict[str, Any]:
    if blocker is None:
        return {
            "same_sha.json": {"runs": []},
            "history.json": {"previous_failures": []},
            "outage.json": {"incidents": []},
        }
    return {
        "same_sha.json": {"runs": blocker._same_sha_runs()},
        "history.json": {"previous_failures": blocker._previous_failures()},
        "outage.json": {"incidents": blocker._incidents()},
    }


def write_sample(
    *,
    settings: Settings,
    name: str,
    prepared: Prepared,
    links: list[Link],
    engine_label: str,
    llm_steps: list[dict[str, Any]],
    skeptic: Any | None = None,
) -> Path:
    """Persist one replayable sample directory. Everything is redacted on the way out."""
    directory = settings.samples_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "llm").mkdir(exist_ok=True)
    (directory / "skeptic").mkdir(exist_ok=True)
    (directory / "zetaris").mkdir(exist_ok=True)

    def dump(relative: str, payload: Any) -> None:
        (directory / relative).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    dump("run.json", _run_payload(prepared.runs))
    dump("diff.json", _diff_payload(prepared.changes))
    dump("logs.json", _logs_payload(prepared))
    dump("pypi.json", _pypi_payload(prepared, settings))

    tables = build_tables(prepared.runs, prepared.changes, prepared.job_logs, prepared.deps)
    dump(
        "zetaris/overlap.json",
        {
            "recorded_from": engine_label,
            "note": "Recorded by redrun record-sample on a live run.",
            "recorded_at": datetime.now().isoformat(),
            "row_count": len(links),
            "rows": [link.model_dump(mode="json") for link in links],
        },
    )
    for filename, payload in _skeptic_payloads(prepared, skeptic).items():
        dump(f"skeptic/{filename}", payload)

    dump(
        "llm/steps.json",
        [
            {
                "text": redact(str(step.get("text", ""))),
                "usage": step.get("usage")
                or {
                    "prompt_tokens": len(json.dumps(step.get("messages", []))) // 4,
                    "completion_tokens": len(str(step.get("text", ""))) // 4,
                    "latency_ms": 0,
                },
                "lane": step.get("lane", "fast"),
            }
            for step in llm_steps
        ],
    )
    (directory / "world.json").write_text(
        json.dumps(
            {
                "note": "entity and fact counts at record time",
                "tables": {name: len(rows) for name, rows in tables.items()},
                "engine": engine_label,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return directory


class RecordingLLM:
    """Wraps a client and keeps every (lane, text, usage) for steps.json."""

    def __init__(self, inner):
        self.inner = inner
        self.steps: list[dict[str, Any]] = []

    def complete(self, messages, lane="fast"):
        completion = self.inner.complete(messages, lane=lane)
        self.steps.append(
            {
                "lane": lane,
                "text": completion.text,
                "messages": messages,
                "usage": {
                    "prompt_tokens": completion.usage.prompt_tokens,
                    "completion_tokens": completion.usage.completion_tokens,
                    "latency_ms": completion.usage.latency_ms,
                },
            }
        )
        return completion

    def __getattr__(self, item):
        return getattr(self.inner, item)


def collect_context_only(url: str, settings: Settings) -> Prepared:
    """Re-exported for the CLI so recording uses the same collector path."""
    return CollectContext(settings=settings)  # pragma: no cover - replaced at call site
