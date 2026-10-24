"""Skeptic: tool checks that can contradict the investigator's verdict.

`status` means "does this check support the proposed cause": pass supports it,
fail contradicts it, n/a does not apply. A fail on a decisive check sends the
investigator back for exactly one revision.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from redrun.adapters.llm import LLMClient
from redrun.adapters.statuspage import StatusPageClient
from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs as logs_collector
from redrun.config import ROOT, Settings
from redrun.core.citations import guard
from redrun.core.confidence import DECISIVE_CHECKS
from redrun.core.hints import test_ids
from redrun.core.models import CheckResult, Evidence, Link, SkepticReport, Verdict
from redrun.core.redact import redact
from redrun.core.taxonomy import Cause
from redrun.trace import Tracer

AGENT = "skeptic"
OVERLAP_BEFORE = timedelta(minutes=30)
OVERLAP_AFTER = timedelta(hours=2)
LIVE_HISTORY_RUNS = 20
LIVE_LOG_FETCHES = 3

# Causes for which "the same sha succeeded somewhere else" is a contradiction.
SENSITIVE_TO_SHA = (Cause.BAD_COMMIT,)
# Causes that a passing-elsewhere finding actually supports.
SUPPORTED_BY_SHA = (Cause.FLAKY_TEST, Cause.INFRA_OUTAGE)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class Skeptic:
    def __init__(
        self,
        *,
        settings: Settings,
        tracer: Tracer,
        runs: github_runs.RunsCollected,
        changes: diff_collector.DiffCollected,
        job_logs: logs_collector.LogsCollected,
        deps: deps_collector.DepsCollected,
        links: list[Link],
        evidence: list[Evidence],
        sample_case: str | None = None,
        github: Any | None = None,
        statuspage: StatusPageClient | None = None,
        judge: LLMClient | None = None,
    ):
        self.settings = settings
        self.tracer = tracer
        self.runs = runs
        self.changes = changes
        self.job_logs = job_logs
        self.deps = deps
        self.links = links
        self.evidence = evidence
        self.sample_case = sample_case
        self.github = github
        self.statuspage = statuspage
        self.judge = judge
        self.last_outage_evidence: list[Evidence] = []

    # ------------------------------------------------------------------ sources

    def _fixture(self, name: str, default: dict) -> dict:
        if not self.sample_case:
            return default
        path = self.settings.samples_dir / self.sample_case / "skeptic" / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return default

    def _same_sha_runs(self) -> list[dict]:
        if self.sample_case:
            return list(self._fixture("same_sha.json", {"runs": []}).get("runs") or [])
        if self.github is None:
            return []
        raw = self.github.list_workflow_runs(
            self.runs.owner,
            self.runs.repo,
            head_sha=self.runs.failed_run.head_sha,
            per_page=30,
        )
        return [
            {
                "run_id": int(run["id"]),
                "workflow": run.get("name", ""),
                "conclusion": run.get("conclusion"),
                "head_sha": run.get("head_sha"),
            }
            for run in raw
        ]

    def _failing_test_ids(self) -> list[str]:
        found: list[str] = []
        for match in self.job_logs.matches:
            for node in test_ids(match.text):
                if node not in found:
                    found.append(node)
        return found

    def _previous_failures(self) -> list[dict]:
        if self.sample_case:
            payload = self._fixture("history.json", {"previous_failures": []})
            return list(payload.get("previous_failures") or [])
        if self.github is None:
            return []

        wanted = set(self._failing_test_ids())
        if not wanted:
            return []
        owner, repo = self.runs.owner, self.runs.repo
        raw = self.github.list_workflow_runs(
            owner, repo, branch=self.runs.failed_run.branch, status="failure", per_page=LIVE_HISTORY_RUNS
        )
        found: list[dict] = []
        for run in raw[:LIVE_LOG_FETCHES]:
            if int(run["id"]) == self.runs.failed_run.run_id:
                continue
            try:
                jobs = self.github.get_jobs(owner, repo, int(run["id"]))
            except Exception:  # noqa: BLE001 - a missing log is not a failed check
                continue
            for job in jobs[:2]:
                try:
                    text = self.github.download_logs(owner, repo, int(job["id"]))
                except Exception:  # noqa: BLE001
                    continue
                for node in sorted(wanted & set(test_ids(text))):
                    found.append(
                        {"test": node, "sha": run.get("head_sha", ""), "run_id": int(run["id"])}
                    )
        return found

    def _incidents(self) -> list[dict]:
        if self.sample_case:
            return list(self._fixture("outage.json", {"incidents": []}).get("incidents") or [])
        client = self.statuspage or StatusPageClient(Path(ROOT) / ".cache" / "githubstatus")
        return client.incidents()

    # ------------------------------------------------------------------- checks

    def _same_sha_elsewhere(self, verdict: Verdict) -> CheckResult:
        failed_id = self.runs.failed_run.run_id
        others = [run for run in self._same_sha_runs() if int(run.get("run_id", 0)) != failed_id]
        successes = [run for run in others if run.get("conclusion") == "success"]

        if verdict.cause in SENSITIVE_TO_SHA:
            if successes:
                run = successes[0]
                return CheckResult(
                    check="same_sha_elsewhere",
                    status="fail",
                    detail=(
                        f"head_sha {self.runs.failed_run.head_sha[:7]} succeeded in "
                        f"{run.get('workflow', 'another workflow')} run {run.get('run_id')}, "
                        "so this commit is not uniquely broken"
                    ),
                )
            return CheckResult(
                check="same_sha_elsewhere",
                status="pass" if not others else "pass",
                detail="no other run of this commit succeeded, so the failure is unique to it",
            )

        if verdict.cause in SUPPORTED_BY_SHA:
            if successes:
                return CheckResult(
                    check="same_sha_elsewhere",
                    status="pass",
                    detail=f"same commit succeeded elsewhere ({len(successes)} run(s))",
                )
            return CheckResult(
                check="same_sha_elsewhere",
                status="fail",
                detail="same commit never succeeded elsewhere, so a flake or outage is unsupported",
            )

        return CheckResult(
            check="same_sha_elsewhere",
            status="n/a",
            detail=f"does not apply to {verdict.cause.value}",
        )

    def _test_failure_history(self, verdict: Verdict) -> CheckResult:
        failures = self._previous_failures()
        if not failures:
            return CheckResult(
                check="test_failure_history",
                status="n/a",
                detail="no earlier failure of these tests in the recent history",
            )

        node = failures[0].get("test", "a test")
        if verdict.cause is Cause.FLAKY_TEST:
            return CheckResult(
                check="test_failure_history",
                status="pass",
                detail=f"{node} also failed on unrelated commits, which is the flake signature",
            )
        if verdict.cause is Cause.BAD_COMMIT:
            return CheckResult(
                check="test_failure_history",
                status="fail",
                detail=f"{node} failed before on other commits, so this is not new with this change",
            )
        return CheckResult(
            check="test_failure_history",
            status="n/a",
            detail=f"does not apply to {verdict.cause.value}",
        )

    def _error_change_overlap(self, verdict: Verdict) -> CheckResult:
        cited = set(verdict.cited_evidence_ids)
        cited_logs = {item for item in cited if item.startswith("ev_log")}
        if not cited_logs:
            return CheckResult(
                check="error_change_overlap",
                status="n/a",
                detail="the verdict cites no log error to overlap",
            )

        linked = [
            link
            for link in self.links
            if link.log_evidence_id in cited and link.change_evidence_id in cited
        ]
        if linked:
            return CheckResult(
                check="error_change_overlap",
                status="pass",
                detail=(
                    f"{linked[0].link_type} links {linked[0].log_evidence_id} to "
                    f"{linked[0].change_evidence_id}"
                ),
                evidence_ids=[linked[0].log_evidence_id or "", linked[0].change_evidence_id or ""],
            )
        return CheckResult(
            check="error_change_overlap",
            status="fail",
            detail="the cited log error is not linked to any cited change",
        )

    def _outage_overlap(self, verdict: Verdict) -> CheckResult:
        run_time = self.runs.failed_run.created_at
        window_start = run_time - OVERLAP_BEFORE
        window_end = run_time + OVERLAP_AFTER
        overlapping = []
        for incident in self._incidents():
            start = _parse(incident.get("created_at"))
            if start is None:
                continue
            end = _parse(incident.get("resolved_at")) or window_end
            if start <= window_end and end >= window_start:
                overlapping.append(incident)

        self.last_outage_evidence = [
            Evidence(
                id=f"ev_outage_{incident.get('id', index)}",
                kind="outage",
                source_url=str(incident.get("shortlink") or "https://www.githubstatus.com/"),
                title=str(incident.get("name") or "GitHub incident"),
                excerpt=redact(
                    f"{incident.get('impact', 'unknown')} / {incident.get('status', 'unknown')}"
                ),
                timestamp=_parse(incident.get("created_at")),
                metadata={"incident_id": incident.get("id"), "impact": incident.get("impact")},
            )
            for index, incident in enumerate(overlapping)
        ]

        if overlapping:
            name = overlapping[0].get("name", "an incident")
            detail = f"{len(overlapping)} GitHub incident(s) overlap the run, e.g. {name}"
            if verdict.cause is Cause.INFRA_OUTAGE:
                return CheckResult(check="outage_overlap", status="pass", detail=detail,
                                   evidence_ids=[e.id for e in self.last_outage_evidence])
            return CheckResult(check="outage_overlap", status="fail", detail=detail,
                               evidence_ids=[e.id for e in self.last_outage_evidence])

        if verdict.cause is Cause.INFRA_OUTAGE:
            return CheckResult(
                check="outage_overlap",
                status="fail",
                detail="no GitHub incident overlaps the run, so an outage is unsupported",
            )
        return CheckResult(
            check="outage_overlap", status="pass", detail="no GitHub incident overlaps the run"
        )

    def _dependency_timing(self, verdict: Verdict) -> CheckResult:
        if verdict.cause is not Cause.DEPENDENCY_BUMP:
            return CheckResult(
                check="dependency_timing",
                status="n/a",
                detail=f"does not apply to {verdict.cause.value}",
            )
        cited = set(verdict.cited_evidence_ids)
        cited_deps = [record for record in self.deps.dependencies if record.evidence_id in cited]
        if not cited_deps:
            return CheckResult(
                check="dependency_timing",
                status="fail",
                detail="the verdict cites no dependency evidence",
            )
        timely = [record for record in cited_deps if record.released_between_runs]
        if timely:
            record = timely[0]
            return CheckResult(
                check="dependency_timing",
                status="pass",
                detail=(
                    f"{record.package} {record.new_version} was published between the green "
                    "and failed runs"
                ),
                evidence_ids=[record.evidence_id],
            )
        record = cited_deps[0]
        return CheckResult(
            check="dependency_timing",
            status="fail",
            detail=f"{record.package} {record.new_version} was not published in the window",
            evidence_ids=[record.evidence_id],
        )

    def _citation_validity(self, verdict: Verdict) -> CheckResult:
        report = guard(verdict, {item.id for item in self.evidence})
        if report.ok:
            return CheckResult(
                check="citation_validity",
                status="pass",
                detail=f"{len(verdict.cited_evidence_ids)} citation(s) all exist in this run",
                evidence_ids=list(verdict.cited_evidence_ids),
            )
        return CheckResult(
            check="citation_validity", status="fail", detail=report.reason()
        )

    # ------------------------------------------------------------------- report

    def check(self, verdict: Verdict, *, revisions: int = 0) -> SkepticReport:
        checks = [
            self._same_sha_elsewhere(verdict),
            self._test_failure_history(verdict),
            self._error_change_overlap(verdict),
            self._outage_overlap(verdict),
            self._dependency_timing(verdict),
            self._citation_validity(verdict),
        ]
        failed_decisive = [
            item for item in checks if item.status == "fail" and item.check in DECISIVE_CHECKS
        ]
        report = SkepticReport(
            checks=checks,
            revisions=revisions,
            final_status="failed" if failed_decisive else "passed",
        )
        self.tracer.emit(
            AGENT,
            "check",
            f"skeptic: {sum(1 for c in checks if c.status == 'pass')} pass, "
            f"{sum(1 for c in checks if c.status == 'fail')} fail, "
            f"{sum(1 for c in checks if c.status == 'n/a')} n/a",
            {
                "revision": revisions,
                "final_status": report.final_status,
                "checks": [item.model_dump() for item in checks],
            },
        )
        if failed_decisive:
            self.tracer.emit(
                AGENT,
                "check",
                f"skeptic: {len(failed_decisive)} decisive check(s) failed: "
                f"{', '.join(item.check for item in failed_decisive)}",
                {"revision": revisions, "decisive_failed": [item.check for item in failed_decisive]},
            )
        return report

    def critique_text(self, failed: list[CheckResult]) -> str:
        """The decision is already made; the judge lane may only reword it."""
        base = "The evidence contradicts this verdict: " + "; ".join(
            f"{item.check} failed ({item.detail})" for item in failed
        )
        if self.judge is None:
            return base
        try:
            completion = self.judge.complete(
                [
                    {
                        "role": "system",
                        "content": "Rewrite the critique as one short paragraph. Do not add facts.",
                    },
                    {"role": "user", "content": base},
                ],
                lane="deep",
            )
        except Exception:  # noqa: BLE001 - wording must never break the run
            return base
        return completion.text.strip() or base
