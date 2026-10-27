"""One run end to end: collectors -> tables -> cross-source query -> rules verdict."""

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from redrun.adapters.base import EngineError, QueryEngine
from redrun.adapters.duckdb_engine import DuckDBEngine
from redrun.adapters.github import GitHubClient
from redrun.adapters.llm import LLMClient
from redrun.adapters.replay_engine import ReplayEngine
from redrun.adapters.zetaris import ZetarisEngine
from redrun.agents.investigator import (
    MarkovianInvestigator,
    ProposedVerdict,
)
from redrun.agents.skeptic import Skeptic
from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext, CollectorError
from redrun.config import ROOT, Settings, load_settings
from redrun.core.citations import guard
from redrun.core.confidence import DECISIVE_CHECKS, apply_confidence
from redrun.core.models import (
    Evidence,
    Link,
    MarkovianMetrics,
    Metrics,
    RunResult,
    Verdict,
)
from redrun.core.rules import rule_verdict  # noqa: F401 - kept for the rules-only path
from redrun.core.tables import build_tables, write_tables
from redrun.core.target import parse_target
from redrun.core.taxonomy import Cause
from redrun.trace import Tracer
from redrun.world.context import ContextBuilder
from redrun.world.ingest import ingest
from redrun.world.store import WorldStoreService

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
    client: object | None = None


def prepare(
    target: str | None,
    sample: str | None,
    base: Settings,
    *,
    trace_id: str | None = None,
    sink=None,
) -> Prepared:
    """Run every collector, redact, and write the four evidence tables."""
    parsed = parse_target(target) if target else None
    if sample is None and base.sample_mode:
        raise ValueError(
            "SAMPLE_MODE is on but no sample was selected; pass sample=<name> or set SAMPLE_MODE=false"
        )

    trace_id = trace_id or uuid.uuid4().hex[:12]
    tracer = Tracer(trace_id, base.logs_dir, sink=sink)
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
        if client is not None:
            client.close()
        raise

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
        client=client,
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
            "database": getattr(engine, "catalog", "") or None,
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
    sink=None,
    trace_id: str | None = None,
) -> RunResult:
    """Investigate a failed run. `sample` selects a fixture and forces offline mode."""
    base = settings or load_settings()
    if sample is not None:
        base = replace(base, sample_mode=True)
    if target is None and sample is None:
        raise ValueError("a GitHub target or a sample name is required")

    label = target.strip() if target else f"sample:{sample}"
    started = time.perf_counter()
    trace_id = trace_id or uuid.uuid4().hex[:12]
    tracer = Tracer(trace_id, base.logs_dir, sink=sink)
    tracer.emit(
        "triage",
        "start",
        f"Investigating {label}",
        {"target": target, "sample": sample, "engine": engine or "auto"},
    )
    prepared = prepare(target, sample, base, trace_id=trace_id, sink=sink)
    return _investigate_prepared(prepared, base, engine, label, started, tracer)


def _triage_cause(links: list[Link], job_logs: logs.LogsCollected) -> Cause:
    """Deterministic first guess from the links and the log patterns."""
    kinds = {link.link_type for link in links}
    if "dependency_match" in kinds:
        return Cause.DEPENDENCY_BUMP
    if "secret_or_config_match" in kinds:
        return Cause.EXPIRED_OR_MISSING_SECRET
    if "file_match" in kinds:
        return Cause.BAD_COMMIT
    patterns = {match.pattern for match in job_logs.matches}
    for pattern, cause in (
        ("auth_or_secret", Cause.EXPIRED_OR_MISSING_SECRET),
        ("dependency_resolution", Cause.DEPENDENCY_BUMP),
        ("infra_env", Cause.INFRA_OUTAGE),
        ("timeout", Cause.FLAKY_TEST),
        ("pytest_failure", Cause.BAD_COMMIT),
    ):
        if pattern in patterns:
            return cause
    return Cause.UNKNOWN


def _fallback_next_step(cause: Cause, cited: Sequence[str] = ()) -> str:
    if cause is Cause.FLAKY_TEST:
        return (
            "Re-run the job. If it fails again on the same commit, treat it as a "
            "real break rather than a flake."
        )
    if cause is Cause.INFRA_OUTAGE and not any(c.startswith("ev_outage_") for c in cited):
        return (
            "Nothing to fix in the code. Check the service the job calls (its status page, "
            "logs, or cold start), then re-run the job once it answers again."
        )
    if cause is Cause.INFRA_OUTAGE:
        return (
            "Nothing to fix in the code. Re-run the failed jobs once githubstatus.com "
            "shows the incident resolved."
        )
    return (
        "Compare the environment change against the failing step, then "
        "fix or revert it and re-run."
    )


def _merge_markovian(first: MarkovianMetrics, extra: MarkovianMetrics) -> MarkovianMetrics:
    tokens_in = first.tokens_in + extra.tokens_in
    return MarkovianMetrics(
        steps=first.steps + extra.steps,
        stop_reason=first.stop_reason,
        tokens_in=tokens_in,
        tokens_out=first.tokens_out + extra.tokens_out,
        carryover_tokens_max=max(first.carryover_tokens_max, extra.carryover_tokens_max),
        full_context_tokens_estimate=first.full_context_tokens_estimate,
        tokens_saved=max(0, first.full_context_tokens_estimate - tokens_in),
        per_step=first.per_step + extra.per_step,
    )


def _verdict_from(proposal: ProposedVerdict) -> Verdict:
    return Verdict(
        cause=proposal.cause,
        root_cause=proposal.root_cause,
        confidence=0.0,
        confidence_breakdown=[],
        next_step=proposal.next_step,
        cited_evidence_ids=list(proposal.cited_evidence_ids),
    )


def _extend_evidence(prepared: Prepared, items: list[Evidence]) -> None:
    """Add the evidence a check produced, once.

    A check runs more than once per case (the revision re-checks, and a refutation
    can lead to a confirm pass), so the same same-sha or incident evidence arrives
    repeatedly. Adding it twice would list one run as two.
    """
    known = {item.id for item in prepared.evidence}
    prepared.evidence.extend(item for item in items if item.id not in known)


def _unknown(reason: str) -> Verdict:
    return Verdict(
        cause=Cause.UNKNOWN,
        root_cause=reason,
        confidence=0.0,
        confidence_breakdown=[],
        next_step="Collect more evidence, or have a human review the run before acting.",
        cited_evidence_ids=[],
    )


def _investigate_prepared(
    prepared: Prepared,
    base: Settings,
    engine: str | None,
    label: str,
    started: float,
    tracer: Tracer,
) -> RunResult:
    llm: LLMClient | None = None
    judge: LLMClient | None = None
    try:
        links, engine_label, _skipped = run_overlap(prepared, base, engine)

        world = ingest(
            trace_id=prepared.trace_id,
            runs=prepared.runs,
            changes=prepared.changes,
            job_logs=prepared.job_logs,
            deps=prepared.deps,
            links=links,
            evidence=prepared.evidence,
        )
        world_path = WorldStoreService(base.data_dir).save(world)
        tracer.emit(
            "collector",
            "progress",
            f"world: {len(world.entities)} entities, {len(world.facts)} facts",
            {
                "entities": len(world.entities),
                "facts": len(world.facts),
                "path": str(world_path),
            },
        )

        candidate = _triage_cause(links, prepared.job_logs)
        tracer.emit(
            "triage",
            "check",
            f"triage candidate: {candidate.value}",
            {"cause": candidate.value, "links": len(links)},
        )

        llm = LLMClient(base, sample_case=prepared.sample)
        investigator = MarkovianInvestigator(
            llm=llm,
            tracer=tracer,
            builder=ContextBuilder(world, prepared.evidence),
            evidence=prepared.evidence,
            max_steps=base.investigator_max_steps,
            chunk_tokens=base.investigator_chunk_tokens,
            carryover_tokens=base.investigator_carryover_tokens,
        )
        investigated = investigator.run(triage_cause=candidate)
        markovian = investigated.metrics
        proposal = investigated.verdict
        verdict = _from_proposal_or_unknown(proposal)

        tracer.emit(
            "investigator",
            "handoff",
            "investigator -> skeptic: verdict proposed",
            {"cause": verdict.cause.value, "cited": verdict.cited_evidence_ids},
        )

        if not base.sample_mode:
            judge = LLMClient(base, sample_case=None)
        skeptic = Skeptic(
            settings=base,
            tracer=tracer,
            runs=prepared.runs,
            changes=prepared.changes,
            job_logs=prepared.job_logs,
            deps=prepared.deps,
            links=links,
            evidence=prepared.evidence,
            sample_case=prepared.sample,
            github=prepared.client,
            judge=judge,
        )
        report = skeptic.check(verdict)
        _extend_evidence(prepared, skeptic.last_outage_evidence + skeptic.last_same_sha_evidence)

        if report.final_status == "failed":
            failed = [
                check
                for check in report.checks
                if check.status == "fail" and check.check in DECISIVE_CHECKS
            ]
            critique = skeptic.critique_text(failed)
            tracer.emit(
                "skeptic",
                "handoff",
                "skeptic -> investigator: critique",
                {"critique": critique, "failed": [check.check for check in failed]},
            )
            revision = investigator.revise(
                critique=critique,
                failed_checks=[check.model_dump() for check in failed],
                carryover=investigated.carryover,
                prior=proposal,
                step_number=markovian.steps + 1,
            )
            markovian = _merge_markovian(markovian, revision.metrics)
            if revision.verdict is not None:
                verdict = _verdict_from(revision.verdict)
            report = skeptic.check(verdict, revisions=1)
            report.final_status = "revised" if report.final_status == "passed" else "failed"
            _extend_evidence(prepared, skeptic.last_outage_evidence + skeptic.last_same_sha_evidence)

        if report.final_status == "failed":
            still_failing = [check.check for check in report.checks if check.status == "fail"]
            # The checks can refute the proposal and still have named the answer. A
            # refutation is only the end of the story if nothing follows from it, so
            # each cause the evidence itself supports gets a full check of its own.
            _extend_evidence(prepared, skeptic.last_same_sha_evidence)
            for cause, detail, cited in skeptic.candidates_from_evidence(report):
                candidate = Verdict(
                    cause=cause,
                    root_cause=detail,
                    confidence=0.0,
                    confidence_breakdown=[],
                    next_step=_fallback_next_step(cause, cited),
                    cited_evidence_ids=cited,
                )
                confirmed = skeptic.check(candidate, revisions=report.revisions)
                tracer.emit(
                    "skeptic",
                    "check",
                    f"skeptic: the evidence supports {cause.value}",
                    {"candidate": cause.value, "confirmed": confirmed.final_status},
                )
                if confirmed.final_status == "passed":
                    verdict, report = candidate, confirmed
                    break
            if report.final_status == "failed":
                verdict = _unknown(
                    "the skeptic rejected every proposed cause after one revision "
                    f"({', '.join(still_failing)})"
                )

        citation = guard(verdict, {item.id for item in prepared.evidence})
        if not citation.ok:
            tracer.emit(
                "investigator",
                "check",
                f"citation guard rejected the verdict: {citation.reason()}",
                {"problems": [problem.code for problem in citation.problems]},
            )
            verdict = _unknown(f"the verdict failed the citation guard ({citation.reason()})")

        against: list[str] = []
        if investigated.carryover.hypotheses:
            against = investigated.carryover.hypotheses[0].against_ids
        verdict = apply_confidence(
            verdict,
            [check.model_dump() for check in report.checks],
            report.revisions,
            links,
            against,
        )

        result = RunResult(
            run_id=str(prepared.runs.failed_run.run_id),
            input=label,
            failed_run=prepared.runs.failed_run,
            last_green_run=prepared.runs.last_green_run,
            evidence=prepared.evidence,
            verdict=verdict,
            links=links,
            skeptic=report,
            trace_id=prepared.trace_id,
            metrics=Metrics(
                llm_calls=markovian.steps,
                tokens_in=markovian.tokens_in,
                tokens_out=markovian.tokens_out,
                latency_ms=int((time.perf_counter() - started) * 1000),
                query_engine=engine_label,
                markovian=markovian,
            ),
        )
        tracer.emit(
            "investigator",
            "result",
            f"{verdict.cause.value}: {verdict.next_step}",
            {
                "cause": verdict.cause.value,
                "confidence": verdict.confidence,
                "skeptic": report.final_status,
                "evidence": len(prepared.evidence),
                "links": len(links),
                "query_engine": engine_label,
                "trace_id": prepared.trace_id,
            },
        )
        result_path = Path(base.data_dir) / prepared.trace_id / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        if prepared.client is not None:
            prepared.client.close()
        if llm is not None:
            llm.close()
        if judge is not None:
            judge.close()


def _from_proposal_or_unknown(proposal: ProposedVerdict | None) -> Verdict:
    if proposal is None:
        return _unknown("the investigator produced no verdict")
    return _verdict_from(proposal)
