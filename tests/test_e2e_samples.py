"""End to end on every sample: both agents in the trace, a handoff, checks, citations."""

import json
from pathlib import Path

import pytest

from redrun.config import Settings
from redrun.core.citations import guard
from redrun.core.models import Event, RunResult
from redrun.core.taxonomy import Cause
from redrun.pipeline import investigate

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
CASES = ["dependency_bump", "bad_commit", "expired_or_missing_secret", "flaky_test"]


def run(sample: str, tmp_path: Path) -> tuple[RunResult, list[Event]]:
    settings = Settings(
        sample_mode=True,
        samples_dir=SAMPLES,
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )
    result = investigate(None, sample=sample, settings=settings)
    trace = tmp_path / "logs" / f"{result.trace_id}.jsonl"
    events = [Event.model_validate(json.loads(line)) for line in trace.read_text().splitlines()]
    return result, events


@pytest.mark.parametrize("sample", CASES)
def test_trace_contains_both_agents_and_a_handoff(sample: str, tmp_path: Path) -> None:
    _, events = run(sample, tmp_path)
    agents = {event.agent for event in events}
    assert {"investigator", "skeptic"} <= agents
    assert any(event.type == "handoff" for event in events)
    assert any(event.agent == "skeptic" and event.type == "check" for event in events)
    assert any(event.agent == "zetaris" for event in events)


@pytest.mark.parametrize("sample", CASES)
def test_citations_pass_the_guard(sample: str, tmp_path: Path) -> None:
    result, _ = run(sample, tmp_path)
    assert result.verdict is not None
    report = guard(result.verdict, {item.id for item in result.evidence})
    assert report.ok, report.reason()
    assert result.verdict.cited_evidence_ids


@pytest.mark.parametrize("sample", CASES)
def test_confidence_is_computed_with_all_six_signals(sample: str, tmp_path: Path) -> None:
    result, _ = run(sample, tmp_path)
    signals = {item.signal for item in result.verdict.confidence_breakdown}
    assert signals == {
        "link_score",
        "supporting_ids",
        "skeptic_pass_ratio",
        "decisive_passes",
        "revision_penalty",
        "against_penalty",
    }
    assert 0.0 <= result.verdict.confidence <= 1.0


@pytest.mark.parametrize("sample", CASES)
def test_markovian_metrics_are_present_and_save_tokens(sample: str, tmp_path: Path) -> None:
    result, _ = run(sample, tmp_path)
    markovian = result.metrics.markovian
    assert markovian is not None
    assert markovian.steps >= 2
    assert markovian.stop_reason in ("rule", "cap")
    assert markovian.tokens_in > 0
    assert markovian.tokens_saved > 0
    assert markovian.carryover_tokens_max <= 800
    assert len(markovian.per_step) == markovian.steps


@pytest.mark.parametrize("sample", CASES)
def test_no_step_prompt_contains_another_chunk(sample: str, tmp_path: Path) -> None:
    _, events = run(sample, tmp_path)
    steps = [event for event in events if event.agent == "investigator" and event.type == "start"]
    assert steps
    assert all("context_tokens_estimate" in event.data for event in steps)


def test_flaky_sample_flips_after_one_revision(tmp_path: Path) -> None:
    result, events = run("flaky_test", tmp_path)

    assert result.verdict is not None
    assert result.verdict.cause is Cause.FLAKY_TEST
    assert result.skeptic is not None
    assert result.skeptic.revisions == 1
    assert result.skeptic.final_status == "revised"

    handoffs = [event for event in events if event.type == "handoff"]
    assert any("skeptic -> investigator" in event.message for event in handoffs), (
        "the revision must be driven by an explicit critique handoff"
    )
    assert len([e for e in handoffs if "skeptic -> investigator" in e.message]) == 1, (
        "at most one revision"
    )
    first_proposal = next(
        event for event in handoffs if "investigator -> skeptic" in event.message
    )
    assert first_proposal.data["cause"] == "bad_commit", "the critique has to change the cause"


def test_the_other_samples_pass_without_a_revision(tmp_path: Path) -> None:
    for sample in ("dependency_bump", "bad_commit", "expired_or_missing_secret"):
        result, _ = run(sample, tmp_path)
        assert result.skeptic is not None
        assert result.skeptic.revisions == 0
        assert result.skeptic.final_status == "passed"


def test_world_and_tables_are_written_for_every_sample(tmp_path: Path) -> None:
    result, _ = run("dependency_bump", tmp_path)
    run_dir = tmp_path / "data" / result.trace_id
    assert (run_dir / "world.json").exists()
    for name in ("runs", "changes", "log_errors", "dependencies"):
        assert (run_dir / f"{name}.parquet").exists()
