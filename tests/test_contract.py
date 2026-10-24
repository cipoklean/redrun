"""The contract is frozen: field sets, enum values, and the exported JSON Schemas."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from redrun.core.models import (
    EXPORTED_SCHEMAS,
    ConfidenceSignal,
    Evidence,
    Event,
    Link,
    Metrics,
    RunInfo,
    RunResult,
    Verdict,
)
from redrun.core.taxonomy import Cause

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "docs" / "schemas"


def test_cause_enum_is_frozen() -> None:
    assert {c.value for c in Cause} == {
        "bad_commit",
        "dependency_bump",
        "flaky_test",
        "infra_outage",
        "expired_or_missing_secret",
        "config_change",
        "unknown",
    }


def test_evidence_fields_match_contract() -> None:
    assert set(Evidence.model_fields) == {
        "id",
        "kind",
        "source_url",
        "title",
        "excerpt",
        "timestamp",
        "metadata",
    }


def test_verdict_fields_match_contract() -> None:
    assert set(Verdict.model_fields) == {
        "cause",
        "root_cause",
        "confidence",
        "confidence_breakdown",
        "next_step",
        "cited_evidence_ids",
    }
    assert set(ConfidenceSignal.model_fields) == {"signal", "weight", "value"}


def test_run_result_fields_match_contract() -> None:
    assert set(RunResult.model_fields) == {
        "run_id",
        "input",
        "failed_run",
        "last_green_run",
        "evidence",
        "verdict",
        "links",
        "trace_id",
        "metrics",
    }
    assert set(Metrics.model_fields) == {
        "llm_calls",
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "query_engine",
    }


def test_link_fields_match_contract() -> None:
    assert set(Link.model_fields) == {
        "link_type",
        "log_evidence_id",
        "change_evidence_id",
        "score_hint",
        "detail",
    }
    with pytest.raises(ValueError):
        Link(link_type="made_up", score_hint=0.5, detail="x")


def test_event_fields_match_contract() -> None:
    assert set(Event.model_fields) == {"ts", "agent", "type", "message", "data"}


def test_event_rejects_unknown_agent_and_type() -> None:
    with pytest.raises(ValueError):
        Event(ts=datetime.now(timezone.utc), agent="narrator", type="start", message="x")
    with pytest.raises(ValueError):
        Event(ts=datetime.now(timezone.utc), agent="triage", type="shrug", message="x")


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValueError):
        Verdict(
            cause=Cause.UNKNOWN,
            root_cause="x",
            confidence=1.4,
            confidence_breakdown=[],
            next_step="y",
            cited_evidence_ids=[],
        )


def test_run_result_serialises_to_json() -> None:
    run = RunInfo(
        run_id=1,
        workflow_name="CI",
        workflow_path=".github/workflows/ci.yml",
        branch="main",
        head_sha="a" * 40,
        created_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
        conclusion="failure",
        runner_image=None,
        jobs=[],
    )
    result = RunResult(
        run_id="1",
        input="acme/widgets",
        failed_run=run,
        last_green_run=None,
        evidence=[],
        verdict=None,
        trace_id="t0",
        metrics=Metrics(),
    )
    payload = json.loads(result.model_dump_json())
    assert payload["run_id"] == "1"
    assert payload["failed_run"]["workflow_name"] == "CI"


@pytest.mark.parametrize("name", sorted(EXPORTED_SCHEMAS))
def test_exported_schema_is_in_sync_with_models(name: str) -> None:
    expected = EXPORTED_SCHEMAS[name].model_json_schema()
    path = SCHEMA_DIR / f"{name}.json"
    assert path.exists(), f"missing exported schema {path}"
    assert json.loads(path.read_text()) == expected, f"{path} is stale; re-export it"
