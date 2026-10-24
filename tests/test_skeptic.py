"""Skeptic checks: each one is a tool call with a pass/fail/n-a verdict."""

import json
from pathlib import Path

import pytest

from redrun.agents.skeptic import DECISIVE_CHECKS, Skeptic
from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Verdict
from redrun.core.taxonomy import Cause
from redrun.trace import Tracer

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def collected(sample: str):
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    changes = diff_collector.collect(ctx, runs)
    job_logs = logs.collect(ctx, runs)
    deps = deps_collector.collect(ctx, runs, changes, job_logs)
    evidence = runs.evidence + changes.evidence + job_logs.evidence + deps.evidence
    return runs, changes, job_logs, deps, evidence


def links_for(sample: str) -> list[dict]:
    path = SAMPLES / sample / "zetaris" / "overlap.json"
    return json.loads(path.read_text())["rows"]


def make(sample: str, tmp_path: Path) -> Skeptic:
    runs, changes, job_logs, deps, evidence = collected(sample)
    from redrun.core.models import Link

    links = [Link(**row) for row in links_for(sample)]
    return Skeptic(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=tmp_path),
        tracer=Tracer("s1", tmp_path),
        runs=runs,
        changes=changes,
        job_logs=job_logs,
        deps=deps,
        links=links,
        evidence=evidence,
        sample_case=sample,
    )


def verdict_for(sample: str, cause: Cause, cited: list[str]) -> Verdict:
    return Verdict(
        cause=cause,
        root_cause=f"{cause.value} because of things",
        confidence=0.5,
        confidence_breakdown=[],
        next_step="do the thing",
        cited_evidence_ids=cited,
    )


def check(report, name: str):
    return next(item for item in report.checks if item.check == name)


# ---------------------------------------------------------------- same sha


def test_same_sha_passing_elsewhere_fails_a_bad_commit_claim(tmp_path: Path) -> None:
    skeptic = make("flaky_test", tmp_path)
    report = skeptic.check(verdict_for("flaky_test", Cause.BAD_COMMIT, ["ev_log_001"]))
    result = check(report, "same_sha_elsewhere")
    assert result.status == "fail"
    assert "succeeded" in result.detail.lower()


def test_same_sha_is_not_applicable_to_a_dependency_bump(tmp_path: Path) -> None:
    skeptic = make("dependency_bump", tmp_path)
    report = skeptic.check(
        verdict_for("dependency_bump", Cause.DEPENDENCY_BUMP, ["ev_log_001", "ev_dep_fastapi"])
    )
    assert check(report, "same_sha_elsewhere").status == "n/a"


# ---------------------------------------------------------------- history


def test_prior_failure_of_the_same_test_contradicts_bad_commit(tmp_path: Path) -> None:
    skeptic = make("flaky_test", tmp_path)
    report = skeptic.check(verdict_for("flaky_test", Cause.BAD_COMMIT, ["ev_log_001"]))
    result = check(report, "test_failure_history")
    assert result.status == "fail"
    assert "test_api.py::test_health_depends_on_time" in result.detail


def test_no_history_is_not_applicable(tmp_path: Path) -> None:
    skeptic = make("dependency_bump", tmp_path)
    report = skeptic.check(
        verdict_for("dependency_bump", Cause.DEPENDENCY_BUMP, ["ev_log_001", "ev_dep_fastapi"])
    )
    assert check(report, "test_failure_history").status == "n/a"


# ---------------------------------------------------------------- overlap


def test_cited_log_and_change_tied_by_a_link_pass(tmp_path: Path) -> None:
    skeptic = make("dependency_bump", tmp_path)
    report = skeptic.check(
        verdict_for("dependency_bump", Cause.DEPENDENCY_BUMP, ["ev_log_001", "ev_dep_fastapi"])
    )
    assert check(report, "error_change_overlap").status == "pass"


def test_citing_a_log_without_its_change_fails_the_overlap(tmp_path: Path) -> None:
    skeptic = make("dependency_bump", tmp_path)
    report = skeptic.check(verdict_for("dependency_bump", Cause.DEPENDENCY_BUMP, ["ev_log_001"]))
    assert check(report, "error_change_overlap").status == "fail"


# ---------------------------------------------------------------- timing


def test_dependency_timing_passes_when_the_release_landed_in_the_window(tmp_path: Path) -> None:
    skeptic = make("dependency_bump", tmp_path)
    report = skeptic.check(
        verdict_for("dependency_bump", Cause.DEPENDENCY_BUMP, ["ev_log_001", "ev_dep_fastapi"])
    )
    assert check(report, "dependency_timing").status == "pass"


def test_dependency_timing_is_not_applicable_to_other_causes(tmp_path: Path) -> None:
    skeptic = make("bad_commit", tmp_path)
    report = skeptic.check(verdict_for("bad_commit", Cause.BAD_COMMIT, ["ev_log_001"]))
    assert check(report, "dependency_timing").status == "n/a"


# ---------------------------------------------------------------- citations


def test_ghost_citations_fail_the_validity_check(tmp_path: Path) -> None:
    skeptic = make("bad_commit", tmp_path)
    report = skeptic.check(verdict_for("bad_commit", Cause.BAD_COMMIT, ["ev_ghost"]))
    assert check(report, "citation_validity").status == "fail"


# ---------------------------------------------------------------- report


def test_a_clean_verdict_passes(tmp_path: Path) -> None:
    skeptic = make("dependency_bump", tmp_path)
    report = skeptic.check(
        verdict_for("dependency_bump", Cause.DEPENDENCY_BUMP, ["ev_log_001", "ev_dep_fastapi"])
    )
    assert report.final_status == "passed"
    assert report.revisions == 0


def test_a_decisive_failure_fails_the_report(tmp_path: Path) -> None:
    skeptic = make("flaky_test", tmp_path)
    report = skeptic.check(verdict_for("flaky_test", Cause.BAD_COMMIT, ["ev_log_001"]))
    assert report.final_status == "failed"
    assert {check(report, name).status for name in DECISIVE_CHECKS} >= {"fail"}


def test_critique_text_is_deterministic_without_a_judge(tmp_path: Path) -> None:
    skeptic = make("flaky_test", tmp_path)
    report = skeptic.check(verdict_for("flaky_test", Cause.BAD_COMMIT, ["ev_log_001"]))
    failed = [item for item in report.checks if item.status == "fail"]
    first = skeptic.critique_text(failed)
    second = skeptic.critique_text(failed)
    assert first == second
    assert "same_sha_elsewhere" in first
