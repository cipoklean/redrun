"""One run end to end: collectors -> tables -> cross-source query -> rules verdict."""

import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from redrun.adapters.base import EngineError, QueryEngine
from redrun.adapters.duckdb_engine import DuckDBEngine
from redrun.adapters.github import GitHubClient
from redrun.adapters.replay_engine import ReplayEngine
from redrun.adapters.zetaris import ZetarisEngine
from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext, CollectorError
from redrun.config import ROOT, Settings, load_settings
from redrun.core.models import Evidence, Link, Metrics, RunResult
from redrun.core.rules import rule_verdict
from redrun.core.tables import build_tables, write_tables
from redrun.core.target import parse_target
from redrun.trace import Tracer

OVERLAP_SQL = ROOT / "redrun" / "queries" / "overlap.sql"


@dataclass
class Prepared:
    trace_id: str
    tracer: Tracer
    sample: str | None
    runs: github_runs.RunsCollected
    changes: diff_collector.DiffCollected
    job_logs: logs.LogsCollected
    deps: deps_collector.DepsCollected
    evidence: list[Evidence]
    table_paths: dict[str, Path]


def prepare(
    target: str | None,
    sample: str | None,
    base: Settings,
    *,
    trace_id: str | None = None,
) -> Prepared:
    """Run every collector, redact, and write the four evidence tables."""
    parsed = parse_target(target) if target else None
    if sample is None and base.sample_mode:
        raise ValueError(
            "SAMPLE_MODE is on but no sample was selected; pass sample=<name> or set SAMPLE_MODE=false"
        )

    trace_id = trace_id or uuid.uuid4().hex[:12]
    tracer = Tracer(trace_id, base.logs_dir)
    ctx = CollectContext(settings=base, target=parsed, sample=sample)
    client = None if ctx.is_sample else GitHubClient(base.github_token)

    try:
        runs = github_runs.collect(ctx, client)
        tracer.emit(
            "collector",
            "progress",
            f"runs: failed #{runs.failed_run.run_id}, "
            + (f"last green #{runs.last_green_run.run_id}" if runs.last_green_run else "no green baseline"),
            {
                "failed_run": runs.failed_run.run_id,
                "last_green_run": runs.last_green_run.run_id if runs.last_green_run else None,
            },
        )
        changes = diff_collector.collect(ctx, runs, client)
        tracer.emit(
            "collector",
            "progress",
            f"diff: {len(changes.commits)} commits, {len(changes.files)} files",
            {"commits": len(changes.commits), "files": len(changes.files)},
        )
        job_logs = logs.collect(ctx, runs, client)
        tracer.emit(
            "collector",
            "progress",
            f"logs: {len(job_logs.matches)} error lines",
            {"matches": len(job_logs.matches)},
        )
        deps = deps_collector.collect(ctx, runs, changes, job_logs)
        tracer.emit(
            "collector",
            "progress",
            f"deps: {len(deps.dependencies)} packages, "
            f"{sum(1 for d in deps.dependencies if d.released_between_runs)} released between runs",
            {"dependencies": len(deps.dependencies)},
        )
    except CollectorError as exc:
        tracer.emit("collector", "error", str(exc))
        raise
    finally:
        if client is not None:
            client.close()

    evidence = runs.evidence + changes.evidence + job_logs.evidence + deps.evidence
    tables = build_tables(runs, changes, job_logs, deps)
    table_paths = write_tables(trace_id, tables, base.data_dir)
    tracer.emit(
        "collector",
        "progress",
        "tables: " + ", ".join(f"{name}={len(rows)}" for name, rows in tables.items()),
        {"tables": {name: len(rows) for name, rows in tables.items()}},
    )
    return Prepared(
        trace_id=trace_id,
        tracer=tracer,
        sample=sample,
        runs=runs,
        changes=changes,
        job_logs=job_logs,
        deps=deps,
        evidence=evidence,
        table_paths=table_paths,
    )


def choose_engine(prepared: Prepared, base: Settings, forced: str | None) -> QueryEngine:
    if forced == "zetaris":
        return ZetarisEngine(base)
    if forced == "duckdb":
        return DuckDBEngine()
    if base.sample_mode and prepared.sample:
        return ReplayEngine(base.samples_dir / prepared.sample)
    if base.zetaris_enabled:
        return ZetarisEngine(base)
    return DuckDBEngine()


def _to_links(rows: Sequence[dict]) -> tuple[list[Link], int]:
    links: list[Link] = []
    skipped = 0
    for row in rows:
        try:
            links.append(
                Link(
                    link_type=row["link_type"],
                    log_evidence_id=row.get("log_evidence_id"),
                    change_evidence_id=row.get("change_evidence_id"),
                    score_hint=float(row["score_hint"]),
                    detail=str(row["detail"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            skipped += 1
    return links, skipped


def run_overlap(
    prepared: Prepared,
    base: Settings,
    forced: str | None,
    engine: QueryEngine | None = None,
) -> tuple[list[Link], str, int]:
    """Run overlap.sql, falling back to DuckDB whenever no engine was forced."""
    sql = OVERLAP_SQL.read_text(encoding="utf-8")
    engine = engine or choose_engine(prepared, base, forced)
    label = engine.engine_id
    tracer = prepared.tracer
    agent = "zetaris" if label.startswith("zetaris") else "collector"
    method = "unknown"

    tracer.emit(
        agent,
        "start",
        f"engine={label} query=overlap.sql",
        {"engine": label, "trace_id": prepared.trace_id},
    )

    try:
        method = engine.register_sources(prepared.trace_id, prepared.table_paths)
        result = engine.query(sql)
    except EngineError as exc:
        tracer.emit(agent, "error", f"engine={label}: {exc}", {"engine": label})
        engine.close()
        if forced is not None:
            raise
        tracer.emit(
            "collector",
            "progress",
            f"falling back to duckdb after {label} failed",
            {"from": label, "to": "duckdb"},
        )
        fallback = DuckDBEngine()
        method = fallback.register_sources(prepared.trace_id, prepared.table_paths)
        result = fallback.query(sql)
        fallback.close()
        label = "duckdb_fallback"
        agent = "collector"

    links, skipped = _to_links(result.rows)
    tracer.emit(
        agent,
        "result",
        f"engine={label} rows={result.row_count} links={len(links)} skipped={skipped} "
        f"latency_ms={result.latency_ms}",
        {
            "engine": label,
            "method": method,
            "rows": result.row_count,
            "links": len(links),
            "skipped": skipped,
            "latency_ms": result.latency_ms,
            "sql_sha256": result.sql_sha256,
        },
    )
    engine.close()
    return links, label, skipped


def investigate(
    target: str | None,
    *,
    sample: str | None = None,
    settings: Settings | None = None,
    engine: str | None = None,
) -> RunResult:
    """Investigate a failed run. `sample` selects a fixture and forces offline mode."""
    base = settings or load_settings()
    if sample is not None:
        base = replace(base, sample_mode=True)
    if target is None and sample is None:
        raise ValueError("a GitHub target or a sample name is required")

    label = target.strip() if target else f"sample:{sample}"
    started = time.perf_counter()
    trace_id = uuid.uuid4().hex[:12]
    Tracer(trace_id, base.logs_dir).emit(
        "triage",
        "start",
        f"Investigating {label}",
        {"target": target, "sample": sample, "engine": engine or "auto"},
    )
    prepared = prepare(target, sample, base, trace_id=trace_id)

    links, engine_label, _skipped = run_overlap(prepared, base, engine)
    verdict = rule_verdict(
        prepared.evidence, prepared.runs.failed_run, prepared.runs.last_green_run, links
    )
    prepared.tracer.emit(
        "investigator",
        "check",
        f"cause={verdict.cause.value} confidence={verdict.confidence}",
        {
            "breakdown": [signal.model_dump() for signal in verdict.confidence_breakdown],
            "cited": verdict.cited_evidence_ids,
            "links": len(links),
        },
    )

    result = RunResult(
        run_id=str(prepared.runs.failed_run.run_id),
        input=label,
        failed_run=prepared.runs.failed_run,
        last_green_run=prepared.runs.last_green_run,
        evidence=prepared.evidence,
        verdict=verdict,
        links=links,
        trace_id=prepared.trace_id,
        metrics=Metrics(
            latency_ms=int((time.perf_counter() - started) * 1000),
            query_engine=engine_label,
        ),
    )
    prepared.tracer.emit(
        "investigator",
        "result",
        f"{verdict.cause.value}: {verdict.next_step}",
        {
            "cause": verdict.cause.value,
            "confidence": verdict.confidence,
            "evidence": len(prepared.evidence),
            "links": len(links),
            "query_engine": engine_label,
            "trace_id": prepared.trace_id,
        },
    )
    return result
