"""The baseline: one plain model call on the same fast lane, and nothing else.

It gets the last 300 redacted log lines and the cause taxonomy. No collectors, no
evidence tables, no cross-source query, no skeptic, no citations, no confidence.
It exists so that the agent's numbers have something honest to sit next to.

The reply is recorded next to the sample (`baseline.json`) so a replay run needs
no network. A missing recording is reported as missing: no reply is ever invented.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from redrun.adapters.llm import LLMClient, LLMError
from redrun.config import Settings
from redrun.core.redact import redact
from redrun.core.taxonomy import Cause

BASELINE_LINES = 300
BASELINE_FILE = "baseline.json"
LANE = "fast"
INVALID = "invalid"

SYSTEM_PROMPT = (
    "You are given the last {lines} lines of the log from a failed GitHub Actions "
    "job, and a closed list of causes. Reply with strict JSON only:\n"
    '{{"cause": "<one of: {causes}>", "root_cause": "<one or two sentences>", '
    '"next_step": "<one sentence>"}}\n'
    "You have not been shown the repository, the diff, the previous green run or "
    "any other source. If the log does not settle the cause, answer "
    '"{unknown}" rather than guessing.'
)

JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class BaselineError(RuntimeError):
    """The baseline could not be recorded."""


def causes() -> list[str]:
    return [cause.value for cause in Cause]


def messages(log_text: str, *, lines: int = BASELINE_LINES) -> list[dict[str, str]]:
    """The whole prompt: a taxonomy and the tail of one job log."""
    body = "\n".join(log_text.splitlines()[-lines:])
    system = SYSTEM_PROMPT.format(lines=lines, causes=", ".join(causes()), unknown=Cause.UNKNOWN.value)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"log tail:\n{body}"},
    ]


def parse(text: str) -> dict[str, str]:
    """Best-effort read of the reply. A cause outside the taxonomy is `invalid`."""
    candidate = text.strip()
    match = JSON_OBJECT.search(candidate)
    if match:
        candidate = match.group(0)
    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(candidate)
        if isinstance(loaded, dict):
            payload = loaded
    except ValueError:
        payload = {}
    cause = str(payload.get("cause") or "").strip().lower()
    if cause not in causes():
        cause = INVALID
    return {
        "cause": cause,
        "root_cause": str(payload.get("root_cause") or "").strip(),
        "next_step": str(payload.get("next_step") or "").strip(),
    }


def tail_from_sample(sample_directory: Path, *, lines: int = BASELINE_LINES) -> str:
    """The last `lines` lines of the failed job's recorded log."""
    path = Path(sample_directory) / "logs.json"
    if not path.exists():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = payload.get("jobs") or []
    failing = [job for job in jobs if job.get("log")] or jobs
    if not failing:
        return ""
    text = "\n".join(str(job.get("log") or "") for job in failing)
    return redact("\n".join(text.splitlines()[-lines:]))


def tail_from_prepared(prepared, *, lines: int = BASELINE_LINES) -> str:
    """The same shape, taken from a live collection's log tails."""
    tails = [
        item.excerpt
        for item in prepared.job_logs.evidence
        if item.metadata.get("tail")
    ]
    if not tails:
        tails = [item.excerpt for item in prepared.job_logs.evidence]
    text = "\n".join(tails)
    return redact("\n".join(text.splitlines()[-lines:]))


def call(settings: Settings, log_text: str, *, transport=None) -> dict[str, Any]:
    """One completion on the fast lane, through the same adapter the agent uses."""
    if settings.sample_mode:
        raise BaselineError(
            "recording a baseline needs live settings (SAMPLE_MODE=false); a replay reads it"
        )
    live = replace(settings, sample_mode=False)
    client = LLMClient(live, sample_case=None, transport=transport)
    try:
        completion = client.complete(messages(log_text), lane=LANE)
    except LLMError as exc:
        raise BaselineError(f"the baseline model could not answer: {exc}") from exc
    finally:
        client.close()
    usage = completion.usage
    reply = parse(completion.text)
    return {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "lane": LANE,
        "provider": usage.provider,
        "model": usage.model,
        "prompt_lines": BASELINE_LINES,
        "reply": reply,
        "raw": completion.text,
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "latency_ms": usage.latency_ms,
        },
        "note": (
            "The baseline sees only the last "
            f"{BASELINE_LINES} redacted log lines and the cause taxonomy: no collectors, "
            "no evidence tables, no cross-source query, no skeptic, no citations."
        ),
    }


def record(
    settings: Settings, sample_directory: Path, log_text: str, *, transport=None
) -> dict[str, Any]:
    payload = call(settings, log_text, transport=transport)
    target = Path(sample_directory) / BASELINE_FILE
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load(sample_directory: Path) -> dict[str, Any] | None:
    path = Path(sample_directory) / BASELINE_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def observation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalise a recorded baseline into the shape the metrics read."""
    if not payload:
        return None
    reply = payload.get("reply") or {}
    usage = payload.get("usage") or {}
    return {
        "cause": str(reply.get("cause") or INVALID),
        "root_cause": str(reply.get("root_cause") or ""),
        "next_step": str(reply.get("next_step") or ""),
        "model": str(payload.get("model") or ""),
        "latency_ms": int(usage.get("latency_ms") or 0),
        "tokens_in": int(usage.get("prompt_tokens") or 0),
        "tokens_out": int(usage.get("completion_tokens") or 0),
        "recorded_at": payload.get("recorded_at"),
    }
