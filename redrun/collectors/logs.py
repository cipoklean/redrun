"""Error lines from the failed job logs, with context windows and a tail."""

import re
from dataclasses import dataclass, field
from typing import Any

from redrun.collectors.base import CollectContext, CollectorError, GitHubAPI
from redrun.collectors.github_runs import RunsCollected
from redrun.core.logtext import strip_timestamp
from redrun.core.models import Evidence
from redrun.core.redact import redact

CONTEXT_LINES = 20
TAIL_LINES = 300

# Order matters: the first pattern that matches a line names that line's evidence.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("traceback", re.compile(r"^Traceback \(most recent call last\):")),
    ("pytest_failure", re.compile(r"^(?:FAILED|ERROR)\s+\S+")),
    (
        "dependency_resolution",
        re.compile(
            r"(?i)(ResolutionImpossible|no solution|could not find a version"
            r"|no matching distribution|conflicting dependencies|version solving failed"
            r"|failed to prepare distributions|requires .+ but .+ is installed)"
        ),
    ),
    (
        "auth_or_secret",
        re.compile(
            r"(?i)(\b401\b|\b403\b|unauthorized|forbidden|permission denied"
            r"|bad credentials|invalid token|authentication required|secret .+ is empty)"
        ),
    ),
    ("timeout", re.compile(r"(?i)(timed out|timeout|deadline exceeded)")),
    (
        "infra_env",
        re.compile(
            r"(?i)(no space left on device|disk quota|out of memory|oom-kill"
            r"|connection reset|connection refused|network is unreachable"
            r"|temporary failure in name resolution|could not resolve host|tls handshake)"
        ),
    ),
    ("python_error", re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)(?::.*)?$")),
)


@dataclass
class LogMatch:
    job: str
    line: int
    pattern: str
    evidence_id: str = ""
    text: str = ""


@dataclass
class LogsCollected:
    evidence: list[Evidence] = field(default_factory=list)
    matches: list[LogMatch] = field(default_factory=list)


def _strip_timestamp(line: str) -> str:
    """Raw GitHub logs prefix every line with an ISO timestamp."""
    return _TIMESTAMP.sub("", line)


def _hits(lines: list[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        body = strip_timestamp(line)
        for name, pattern in PATTERNS:
            if pattern.search(body):
                found.append((index, name))
                break
    return found


def _windows(lines: list[str], context: int = CONTEXT_LINES) -> list[dict[str, Any]]:
    """One window per cluster of matches, so a traceback is not repeated 12 times."""
    windows: list[dict[str, Any]] = []
    for index, name in _hits(lines):
        start = max(0, index - context)
        end = min(len(lines), index + context + 1)
        if windows and start <= windows[-1]["end"]:
            current = windows[-1]
            current["end"] = max(current["end"], end)
            current["patterns"].append(name)
            current["hits"].append(index)
        else:
            windows.append({"start": start, "end": end, "patterns": [name], "hits": [index]})
    return windows


def _tail(lines: list[str]) -> list[str]:
    return lines[-TAIL_LINES:]


def _job_logs(
    ctx: CollectContext, runs: RunsCollected, client: GitHubAPI | None
) -> list[tuple[str, int, str]]:
    jobs = [j for j in runs.failed_run.jobs if j.conclusion != "success"] or list(
        runs.failed_run.jobs
    )
    if ctx.is_sample:
        data = ctx.fixture("logs.json")
        by_id = {int(j["job_id"]): j for j in data.get("jobs", [])}
        out: list[tuple[str, int, str]] = []
        for job in jobs:
            raw = by_id.get(job.job_id)
            if raw is None:
                continue
            out.append((str(raw.get("name", job.name)), int(raw["job_id"]), str(raw.get("log", ""))))
        return out

    if client is None:
        raise CollectorError("a GitHub client is required outside sample mode")
    return [
        (job.name, job.job_id, client.download_logs(runs.owner, runs.repo, job.job_id))
        for job in jobs
    ]


def collect(
    ctx: CollectContext, runs: RunsCollected, client: GitHubAPI | None = None
) -> LogsCollected:
    out = LogsCollected()
    counter = 0
    for job_name, job_id, text in _job_logs(ctx, runs, client):
        lines = text.splitlines()
        if not lines:
            continue
        for window in _windows(lines):
            counter += 1
            first_hit = window["hits"][0]
            pattern = window["patterns"][0]
            evidence_id = f"ev_log_{counter:03d}"
            out.matches.append(
                LogMatch(
                    job=job_name,
                    line=first_hit + 1,
                    pattern=pattern,
                    evidence_id=evidence_id,
                    text=strip_timestamp(lines[first_hit]).strip(),
                )
            )
            out.evidence.append(
                Evidence(
                    id=evidence_id,
                    kind="log",
                    source_url=(
                        f"https://github.com/{runs.owner}/{runs.repo}/actions/runs/"
                        f"{runs.failed_run.run_id}/job/{job_id}"
                    ),
                    title=f"{pattern} at line {first_hit + 1} in {job_name}",
                    excerpt=redact("\n".join(lines[window["start"] : window["end"]])),
                    timestamp=runs.failed_run.created_at,
                    metadata={
                        "job": job_name,
                        "job_id": job_id,
                        "line": first_hit + 1,
                        "patterns": window["patterns"],
                        "tail": False,
                    },
                )
            )
        tail = _tail(lines)
        out.evidence.append(
            Evidence(
                id=f"ev_log_tail_{job_id}",
                kind="log",
                source_url=(
                    f"https://github.com/{runs.owner}/{runs.repo}/actions/runs/"
                    f"{runs.failed_run.run_id}/job/{job_id}?tail=1"
                ),
                title=f"Log tail ({len(tail)} lines) of {job_name}",
                excerpt=redact("\n".join(tail)),
                timestamp=runs.failed_run.created_at,
                metadata={
                    "job": job_name,
                    "job_id": job_id,
                    "lines": len(tail),
                    "tail": True,
                },
            )
        )
    return out
