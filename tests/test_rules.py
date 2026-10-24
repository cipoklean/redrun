"""The rules verdict is deterministic and must account for its own confidence."""

from pathlib import Path

import pytest

from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Evidence
from redrun.core.rules import rule_verdict
from redrun.core.taxonomy import Cause

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def collect(sample: str):
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    d = diff_collector.collect(ctx, runs)
    lg = logs.collect(ctx, runs)
    return runs, runs.evidence + d.evidence + lg.evidence


@pytest.mark.parametrize(
    "sample,expected",
    [
        ("dependency_bump", Cause.DEPENDENCY_BUMP),
        ("bad_commit", Cause.BAD_COMMIT),
        ("expired_or_missing_secret", Cause.EXPIRED_OR_MISSING_SECRET),
    ],
)
def test_rules_pick_the_expected_cause(sample: str, expected: Cause) -> None:
    runs, evidence = collect(sample)
    verdict = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    assert verdict.cause is expected


@pytest.mark.parametrize("sample", ["dependency_bump", "bad_commit", "expired_or_missing_secret"])
def test_confidence_is_the_sum_of_its_signals(sample: str) -> None:
    runs, evidence = collect(sample)
    verdict = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    recomputed = sum(s.weight * s.value for s in verdict.confidence_breakdown)
    assert verdict.confidence == pytest.approx(min(0.95, round(recomputed, 2)))
    assert 0.0 <= verdict.confidence <= 1.0


@pytest.mark.parametrize("sample", ["dependency_bump", "bad_commit", "expired_or_missing_secret"])
def test_verdict_cites_evidence_that_exists(sample: str) -> None:
    runs, evidence = collect(sample)
    verdict = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    ids = {e.id for e in evidence}
    assert verdict.cited_evidence_ids
    assert set(verdict.cited_evidence_ids) <= ids


@pytest.mark.parametrize("sample", ["dependency_bump", "bad_commit", "expired_or_missing_secret"])
def test_verdict_is_stable_across_calls(sample: str) -> None:
    runs, evidence = collect(sample)
    first = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    second = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    assert first == second


def test_no_evidence_is_unknown_with_zero_confidence() -> None:
    runs, _ = collect("bad_commit")
    verdict = rule_verdict([], runs.failed_run, runs.last_green_run)
    assert verdict.cause is Cause.UNKNOWN
    assert verdict.confidence == 0.0
    assert verdict.cited_evidence_ids == []


def test_confidence_is_capped_even_with_every_signal_firing() -> None:
    every_line = (
        "Traceback (most recent call last):\n"
        "FAILED tests/test_x.py::test_y\n"
        "ModuleNotFoundError: No module named 'fastapi'\n"
        "ERROR: no solution, version solving failed\n"
        "401 Unauthorized: bad credentials\n"
        "connection refused by registry\n"
        "No space left on device\n"
        "timed out after 30s\n"
    )
    evidence = [
        Evidence(
            id="ev_log_001",
            kind="log",
            source_url="https://example.test/job/1",
            title="everything",
            excerpt=every_line,
            metadata={"tail": False, "filename": "pyproject.toml"},
        ),
        Evidence(
            id="ev_file_001",
            kind="file",
            source_url="https://example.test/blob/main/pyproject.toml",
            title="pyproject.toml",
            excerpt='+    "fastapi>=0.116",',
            metadata={"filename": "pyproject.toml"},
        ),
    ]
    runs, _ = collect("bad_commit")
    verdict = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    assert verdict.confidence <= 0.95


def test_root_cause_and_next_step_are_grounded_not_boilerplate() -> None:
    runs, evidence = collect("expired_or_missing_secret")
    verdict = rule_verdict(evidence, runs.failed_run, runs.last_green_run)
    assert "401" in verdict.root_cause or "credential" in verdict.root_cause.lower()
    assert verdict.next_step
    assert verdict.next_step != verdict.root_cause
