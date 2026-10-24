"""Rules must read the log the same way the extractor does."""

from pathlib import Path

import pytest

from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Link
from redrun.core.rules import rule_verdict
from redrun.core.taxonomy import Cause

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def verdict_for(sample: str):
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    evidence = runs.evidence + diff_collector.collect(ctx, runs).evidence + logs.collect(ctx, runs).evidence
    # The real pipeline feeds links in; the file hint here comes from the same table shape.
    links = [
        Link(
            link_type="file_match",
            log_evidence_id="ev_log_001",
            change_evidence_id="ev_file_001",
            score_hint=0.8,
            detail="synthetic and only for the confidence floor assertion",
        )
    ]
    return rule_verdict(evidence, runs.failed_run, runs.last_green_run, links)


def test_raw_log_timestamps_do_not_hide_anchored_signals() -> None:
    """Fixtures keep ISO timestamps; a `^`-anchored pattern must still see the line."""
    verdict = verdict_for("bad_commit")
    fired = {s.signal for s in verdict.confidence_breakdown if s.value}
    assert "log_python_error" in fired
    assert "log_test_failure" in fired


def test_bad_commit_clears_the_threshold_comfortably() -> None:
    verdict = verdict_for("bad_commit")
    assert verdict.cause is Cause.BAD_COMMIT
    assert verdict.confidence >= 0.7


def test_quoted_root_cause_does_not_leak_log_timestamps() -> None:
    verdict = verdict_for("bad_commit")
    assert "2026-10-23T" not in verdict.root_cause
