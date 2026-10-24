"""investigate() wires the collectors and the rules into one RunResult plus a trace."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from redrun.adapters.base import EngineError
from redrun.config import Settings
from redrun.core.models import Event, RunResult
from redrun.core.taxonomy import Cause
from redrun.pipeline import investigate, prepare, run_overlap

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        sample_mode=True,
        samples_dir=SAMPLES,
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )


def test_sample_run_produces_a_complete_result(tmp_path: Path) -> None:
    result = investigate(None, sample="dependency_bump", settings=settings(tmp_path))
    assert isinstance(result, RunResult)
    assert result.run_id == "5001"
    assert result.input == "sample:dependency_bump"
    assert result.failed_run.conclusion == "failure"
    assert result.last_green_run is not None
    assert result.verdict is not None
    assert result.verdict.cause is Cause.DEPENDENCY_BUMP
    assert result.verdict.confidence > 0.5
    assert result.metrics.llm_calls == 0
    assert len(result.evidence) >= 5


def test_result_round_trips_through_json(tmp_path: Path) -> None:
    result = investigate(None, sample="bad_commit", settings=settings(tmp_path))
    payload = json.loads(result.model_dump_json())
    assert payload["verdict"]["cause"] == "bad_commit"
    validated = RunResult.model_validate(payload)
    assert validated.trace_id == result.trace_id


def test_every_run_writes_a_trace_with_a_result_event(tmp_path: Path) -> None:
    result = investigate(None, sample="bad_commit", settings=settings(tmp_path))
    path = tmp_path / "logs" / f"{result.trace_id}.jsonl"
    assert path.exists()
    events = [Event.model_validate(json.loads(line)) for line in path.read_text().splitlines()]
    assert len(events) >= 4
    assert events[0].type == "start"
    assert events[-1].type == "result"
    assert {e.agent for e in events} >= {"triage", "collector", "investigator"}


def test_evidence_ids_are_unique(tmp_path: Path) -> None:
    result = investigate(None, sample="expired_or_missing_secret", settings=settings(tmp_path))
    ids = [e.id for e in result.evidence]
    assert len(ids) == len(set(ids))


def test_target_is_kept_as_the_input_label(tmp_path: Path) -> None:
    result = investigate("acme/widgets", sample="bad_commit", settings=settings(tmp_path))
    assert result.input == "acme/widgets"


def test_missing_target_and_sample_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        investigate(None, sample=None, settings=settings(tmp_path))


def test_invalid_target_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        investigate("not a repo", sample=None, settings=settings(tmp_path))


def test_sample_mode_reports_the_replay_engine(tmp_path: Path) -> None:
    result = investigate(None, sample="dependency_bump", settings=settings(tmp_path))
    assert result.metrics.query_engine == "zetaris_replay"


def test_forced_duckdb_overrides_the_replay(tmp_path: Path) -> None:
    result = investigate(None, sample="bad_commit", settings=settings(tmp_path), engine="duckdb")
    assert result.metrics.query_engine == "duckdb"


def test_links_come_from_the_cross_source_query(tmp_path: Path) -> None:
    result = investigate(None, sample="dependency_bump", settings=settings(tmp_path))
    assert result.links, "the overlap query should have produced a link"
    link = result.links[0]
    assert link.link_type == "dependency_match"
    assert link.log_evidence_id in {e.id for e in result.evidence}
    assert "fastapi" in link.detail


def test_links_are_cited_by_the_verdict(tmp_path: Path) -> None:
    result = investigate(None, sample="bad_commit", settings=settings(tmp_path))
    assert result.links
    cited = set(result.verdict.cited_evidence_ids)
    assert {link.log_evidence_id for link in result.links} <= cited


def test_evidence_tables_are_written_under_the_trace_id(tmp_path: Path) -> None:
    result = investigate(None, sample="bad_commit", settings=settings(tmp_path))
    run_dir = tmp_path / "data" / result.trace_id
    for name in ("runs", "changes", "log_errors", "dependencies"):
        assert (run_dir / f"{name}.parquet").exists(), name
        assert (run_dir / f"{name}.csv").exists(), name


def test_trace_records_the_engine_with_the_zetaris_agent(tmp_path: Path) -> None:
    result = investigate(None, sample="dependency_bump", settings=settings(tmp_path))
    path = tmp_path / "logs" / f"{result.trace_id}.jsonl"
    events = [Event.model_validate(json.loads(line)) for line in path.read_text().splitlines()]
    zetaris_events = [e for e in events if e.agent == "zetaris"]
    assert {e.type for e in zetaris_events} == {"start", "result"}
    assert zetaris_events[0].data["engine"] == "zetaris_replay"
    assert "sql_sha256" in zetaris_events[1].data
    assert "latency_ms" in zetaris_events[1].data


def test_trace_never_contains_sql_credentials(tmp_path: Path) -> None:
    base = replace(
        settings(tmp_path),
        zetaris_url="http://localhost:8888/api/v1.0",
        zetaris_token="super-secret-token",
    )
    result = investigate(None, sample="bad_commit", settings=base, engine="duckdb")
    raw = (tmp_path / "logs" / f"{result.trace_id}.jsonl").read_text()
    assert "super-secret-token" not in raw


def test_zetaris_failure_falls_back_to_duckdb(tmp_path: Path) -> None:
    base = settings(tmp_path)
    prepared = prepare(None, "bad_commit", base)
    dead = replace(
        base,
        sample_mode=False,
        zetaris_enabled=True,
        zetaris_url="http://127.0.0.1:9/api/v1.0",
        zetaris_token="dead-token",
    )
    links, label, _skipped = run_overlap(prepared, dead, None)

    assert label == "duckdb_fallback"
    assert links and links[0].link_type == "file_match"

    events = [
        Event.model_validate(json.loads(line))
        for line in (tmp_path / "logs" / f"{prepared.trace_id}.jsonl").read_text().splitlines()
    ]
    assert any(e.agent == "zetaris" and e.type == "error" for e in events)
    assert any("falling back" in e.message for e in events)


def test_forced_zetaris_does_not_silently_fall_back(tmp_path: Path) -> None:
    base = settings(tmp_path)
    prepared = prepare(None, "bad_commit", base)
    dead = replace(
        base,
        sample_mode=False,
        zetaris_url="http://127.0.0.1:9/api/v1.0",
        zetaris_token="dead-token",
    )
    with pytest.raises(EngineError):
        run_overlap(prepared, dead, "zetaris")
