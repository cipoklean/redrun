"""Deterministic ingest: collector evidence and Zetaris links in, world model out.

No model is involved. Two runs over the same evidence produce byte-identical facts,
which is what makes the world model citable.
"""

from datetime import datetime, timezone
from typing import Any

from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs as logs_collector
from redrun.core.hints import error_class, file_hint, package_hint, test_ids
from redrun.core.models import Evidence, Link
from redrun.world.store import Entity, Fact, WorldModel, FactObject


class _Builder:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.entities: list[Entity] = []
        self.facts: list[Fact] = []
        self._entity_ids: set[str] = set()

    def entity(
        self, entity_id: str, kind: str, label: str, evidence_id: str, **metadata: Any
    ) -> str:
        if entity_id not in self._entity_ids:
            self.entities.append(
                Entity(
                    entity_id=entity_id,
                    kind=kind,  # type: ignore[arg-type]
                    label=label,
                    evidence_id=evidence_id,
                    metadata=metadata,
                )
            )
            self._entity_ids.add(entity_id)
        return entity_id

    def fact(
        self,
        subject: str,
        predicate: str,
        obj: FactObject,
        evidence_id: str,
        collector: str,
        source_url: str,
        observed_at: datetime,
    ) -> None:
        self.facts.append(
            Fact(
                fact_id=f"f{len(self.facts) + 1:03d}",
                subject=subject,
                predicate=predicate,
                object=obj,
                evidence_id=evidence_id,
                collector=collector,
                source_url=source_url,
                observed_at=observed_at,
            )
        )


def _run_facts(builder: _Builder, runs: github_runs.RunsCollected) -> None:
    for role, run in (("green", runs.last_green_run), ("failed", runs.failed_run)):
        if run is None:
            continue
        evidence_id = f"ev_run_{run.run_id}"
        subject = builder.entity(
            f"run:{run.run_id}",
            "run",
            f"{run.workflow_name} #{run.run_id} ({role})",
            evidence_id,
            role=role,
        )
        url = f"https://github.com/{runs.owner}/{runs.repo}/actions/runs/{run.run_id}"
        for predicate, value in (
            ("role", role),
            ("conclusion", run.conclusion),
            ("head_sha", run.head_sha),
            ("branch", run.branch),
            ("workflow", run.workflow_name),
            ("runner_image", run.runner_image),
        ):
            builder.fact(subject, predicate, value, evidence_id, "github_runs", url, run.created_at)


def _change_facts(
    builder: _Builder, runs: github_runs.RunsCollected, changes: diff_collector.DiffCollected
) -> None:
    failed = runs.failed_run
    failed_subject = f"run:{failed.run_id}"
    for commit in changes.commits:
        subject = builder.entity(
            f"commit:{commit.sha[:7]}",
            "commit",
            f"{commit.sha[:7]} {commit.message.splitlines()[0][:60]}",
            f"ev_commit_{commit.sha[:6]}",
            author=commit.author,
        )
        url = f"https://github.com/{runs.owner}/{runs.repo}/commit/{commit.sha}"
        observed = commit.date or failed.created_at
        for predicate, value in (
            ("message", commit.message.splitlines()[0][:200]),
            ("author", commit.author),
            ("precedes_run", failed_subject),
        ):
            builder.fact(
                subject, predicate, value, f"ev_commit_{commit.sha[:6]}", "diff", url, observed
            )

    for item in changes.evidence:
        if item.kind != "file":
            continue
        filename = str(item.metadata.get("filename", ""))
        subject = builder.entity(f"file:{filename}", "file", filename, item.id)
        observed = failed.created_at
        for predicate, value in (
            ("change_type", str(item.metadata.get("status", "modified"))),
            ("path", filename),
            ("changed_in_run", failed_subject),
        ):
            builder.fact(subject, predicate, value, item.id, "diff", item.source_url, observed)


def _dependency_facts(builder: _Builder, deps: deps_collector.DepsCollected) -> None:
    for record in deps.dependencies:
        evidence = next((e for e in deps.evidence if e.id == record.evidence_id), None)
        if evidence is None:
            continue
        subject = builder.entity(
            f"pkg:{record.package}", "package", record.package, record.evidence_id,
            old=record.old_version, new=record.new_version,
        )
        observed = record.released_at or evidence.timestamp or datetime.now(timezone.utc)
        for predicate, value in (
            ("old_version", record.old_version),
            ("new_version", record.new_version),
            ("released_between_runs", record.released_between_runs),
            (
                "released_at",
                record.released_at.isoformat() if record.released_at else None,
            ),
        ):
            builder.fact(
                subject, predicate, value, record.evidence_id, "pypi", evidence.source_url, observed
            )


def _log_facts(
    builder: _Builder, runs: github_runs.RunsCollected, job_logs: logs_collector.LogsCollected
) -> None:
    windows = {e.id: e for e in job_logs.evidence if e.kind == "log" and not e.metadata.get("tail")}
    failed_subject = f"run:{runs.failed_run.run_id}"
    for match in job_logs.matches:
        evidence = windows.get(match.evidence_id)
        if evidence is None:
            continue
        subject = builder.entity(
            f"log:{match.evidence_id}",
            "log_error",
            f"{match.pattern} in {match.job}",
            match.evidence_id,
            job=match.job,
            line=match.line,
        )
        observed = runs.failed_run.created_at
        builder.fact(
            subject, "error_class", error_class(match.text) or match.pattern,
            match.evidence_id, "logs", evidence.source_url, observed,
        )
        builder.fact(
            subject, "message", match.text[:300], match.evidence_id, "logs",
            evidence.source_url, observed,
        )
        builder.fact(
            subject, "observed_in_run", failed_subject, match.evidence_id, "logs",
            evidence.source_url, observed,
        )
        hint_file = file_hint(match.text) or file_hint(evidence.excerpt)
        if hint_file:
            target = builder.entity(f"file:{hint_file}", "file", hint_file, match.evidence_id)
            builder.fact(subject, "names_file", target, match.evidence_id, "logs",
                         evidence.source_url, observed)
        hint_package = package_hint(match.text) or package_hint(evidence.excerpt)
        if hint_package:
            target = builder.entity(f"pkg:{hint_package}", "package", hint_package, match.evidence_id)
            builder.fact(subject, "names_package", target, match.evidence_id, "logs",
                         evidence.source_url, observed)
        for node_id in test_ids(match.text):
            target = builder.entity(f"test:{node_id}", "test", node_id, match.evidence_id)
            builder.fact(subject, "fails_test", target, match.evidence_id, "logs",
                         evidence.source_url, observed)
            builder.fact(target, "failed_in_run", failed_subject, match.evidence_id, "logs",
                         evidence.source_url, observed)


def _link_facts(builder: _Builder, links: list[Link], evidence: list[Evidence]) -> None:
    by_id = {e.id: e for e in evidence}
    for index, link in enumerate(links):
        log_id = link.log_evidence_id
        change_id = link.change_evidence_id
        anchor = change_id or log_id
        if not anchor:
            continue
        source = by_id.get(anchor)
        url = source.source_url if source else ""
        observed = (source.timestamp if source else None) or datetime.now(timezone.utc)
        if log_id:
            builder.entity(f"log:{log_id}", "log_error", f"log {log_id}", log_id)
            subject = f"log:{log_id}"
        elif change_id:
            subject = f"change:{change_id}"
            builder.entity(subject, "file", f"change {change_id}", change_id)
        else:
            continue
        builder.fact(
            subject,
            f"linked_via_{link.link_type}",
            link.detail[:300],
            anchor,
            "zetaris",
            url,
            observed,
        )
        builder.fact(
            subject, "link_score", float(link.score_hint), anchor, "zetaris", url, observed
        )


def _outage_facts(builder: _Builder, evidence: list[Evidence]) -> None:
    for item in evidence:
        if item.kind != "outage":
            continue
        subject = builder.entity(
            f"outage:{item.id}", "outage", item.title, item.id, name=item.title
        )
        observed = item.timestamp or datetime.now(timezone.utc)
        builder.fact(subject, "name", item.title, item.id, "statuspage", item.source_url, observed)
        builder.fact(subject, "detail", item.excerpt[:300], item.id, "statuspage",
                     item.source_url, observed)


def ingest(
    *,
    trace_id: str,
    runs: github_runs.RunsCollected,
    changes: diff_collector.DiffCollected,
    job_logs: logs_collector.LogsCollected,
    deps: deps_collector.DepsCollected,
    links: list[Link],
    evidence: list[Evidence],
) -> WorldModel:
    builder = _Builder(trace_id)
    _run_facts(builder, runs)
    _change_facts(builder, runs, changes)
    _dependency_facts(builder, deps)
    _log_facts(builder, runs, job_logs)
    _link_facts(builder, links, evidence)
    _outage_facts(builder, evidence)
    return WorldModel(trace_id=trace_id, entities=builder.entities, facts=builder.facts)
