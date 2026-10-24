"""overlap.sql must produce the right link types on all three samples, on DuckDB."""

from pathlib import Path

import pytest

from redrun.adapters.duckdb_engine import DuckDBEngine
from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.tables import build_tables, write_tables

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
OVERLAP_SQL = ROOT / "redrun" / "queries" / "overlap.sql"


def links_for(sample: str, tmp_path: Path) -> list[dict]:
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    changes = diff_collector.collect(ctx, runs)
    job_logs = logs.collect(ctx, runs)
    deps = deps_collector.collect(ctx, runs, changes, job_logs)
    tables = build_tables(runs, changes, job_logs, deps)
    paths = write_tables(f"x_{sample}", tables, tmp_path)
    engine = DuckDBEngine()
    engine.register_sources(f"x_{sample}", paths)
    return engine.query(OVERLAP_SQL.read_text()).rows


def test_sql_file_exists_and_is_one_statement() -> None:
    sql = OVERLAP_SQL.read_text()
    assert sql.strip()
    assert sql.count(";") <= 1, "keep it a single statement so both engines accept it"
    for table in ("runs", "changes", "log_errors", "dependencies"):
        assert table in sql


def test_dependency_sample_links_log_error_to_released_package(tmp_path: Path) -> None:
    rows = links_for("dependency_bump", tmp_path)
    dep = [r for r in rows if r["link_type"] == "dependency_match"]
    assert dep, f"expected a dependency_match, got {rows}"
    assert dep[0]["log_evidence_id"].startswith("ev_log_")
    assert "fastapi" in dep[0]["detail"]


def test_bad_commit_sample_links_log_error_to_changed_file(tmp_path: Path) -> None:
    rows = links_for("bad_commit", tmp_path)
    match = [r for r in rows if r["link_type"] == "file_match"]
    assert match, f"expected a file_match, got {rows}"
    assert match[0]["change_evidence_id"].startswith("ev_file_")
    assert "github_runs.py" in match[0]["detail"]


def test_secret_sample_links_auth_failure_to_workflow_change(tmp_path: Path) -> None:
    rows = links_for("expired_or_missing_secret", tmp_path)
    match = [r for r in rows if r["link_type"] == "secret_or_config_match"]
    assert match, f"expected a secret_or_config_match, got {rows}"
    assert ".github/workflows/deploy.yml" in match[0]["detail"]


def test_rows_are_ranked_by_score(tmp_path: Path) -> None:
    rows = links_for("dependency_bump", tmp_path)
    scores = [float(r["score_hint"]) for r in rows]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("sample", ["dependency_bump", "bad_commit", "expired_or_missing_secret"])
def test_every_row_has_the_contract_columns(sample: str, tmp_path: Path) -> None:
    rows = links_for(sample, tmp_path)
    assert rows, f"{sample} produced no links"
    for row in rows:
        assert set(row) == {
            "link_type",
            "log_evidence_id",
            "change_evidence_id",
            "score_hint",
            "detail",
        }
        assert row["link_type"] in {
            "dependency_match",
            "file_match",
            "secret_or_config_match",
            "config_match",
        }


def test_query_is_repeatable(tmp_path: Path) -> None:
    first = links_for("dependency_bump", tmp_path)
    second = links_for("dependency_bump", tmp_path)
    assert first == second


def test_empty_tables_still_run(tmp_path: Path) -> None:
    engine = DuckDBEngine()
    engine.register_sources("empty", write_tables("empty", {n: [] for n in
        ("runs", "changes", "log_errors", "dependencies")}, tmp_path))
    assert engine.query(OVERLAP_SQL.read_text()).rows == []
