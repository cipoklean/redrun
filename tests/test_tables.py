"""Evidence must land in four typed tables on disk, in Parquet and CSV."""

import csv
from pathlib import Path

import duckdb
import pytest

from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.tables import SCHEMAS, TABLE_NAMES, build_tables, write_tables

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def collected(sample: str = "dependency_bump"):
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    changes = diff_collector.collect(ctx, runs)
    job_logs = logs.collect(ctx, runs)
    deps = deps_collector.collect(ctx, runs, changes, job_logs, cache_dir=None)
    return runs, changes, job_logs, deps, build_tables(runs, changes, job_logs, deps)


def test_table_rows_match_declared_arity() -> None:
    _, _, _, _, tables = collected()
    assert set(tables) == set(TABLE_NAMES)
    for name, columns in SCHEMAS.items():
        for row in tables[name]:
            assert len(row) == len(columns), f"{name} row {row} does not match its schema"


def test_writes_parquet_and_csv_for_all_four_tables(tmp_path: Path) -> None:
    _, _, _, _, tables = collected()
    paths = write_tables("t1", tables, tmp_path)
    for name in TABLE_NAMES:
        assert paths[name].exists(), f"{name}.parquet missing"
        assert (tmp_path / "t1" / f"{name}.csv").exists(), f"{name}.csv missing"


def test_parquet_has_the_expected_schema_and_rows(tmp_path: Path) -> None:
    _, _, _, _, tables = collected()
    write_tables("t2", tables, tmp_path)
    con = duckdb.connect()
    described = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{tmp_path}/t2/runs.parquet')"
    ).fetchall()
    assert [r[0] for r in described] == [
        "run_id", "role", "sha", "started_at", "conclusion", "runner_image", "workflow_file_sha",
    ]
    rows = con.execute(f"SELECT role FROM read_parquet('{tmp_path}/t2/runs.parquet')").fetchall()
    assert sorted(r[0] for r in rows) == ["failed", "green"]


def test_csv_header_matches_column_names(tmp_path: Path) -> None:
    _, _, _, _, tables = collected()
    write_tables("t3", tables, tmp_path)
    with (tmp_path / "t3" / "dependencies.csv").open() as handle:
        header = next(csv.reader(handle))
    assert header == [
        "evidence_id", "package", "old_version", "new_version", "released_at", "released_between_runs",
    ]


def test_log_errors_carry_extracted_hints() -> None:
    _, _, job_logs, _, tables = collected()
    rows = tables["log_errors"]
    assert len(rows) == len(job_logs.matches)
    package_hints = {row[7] for row in rows}
    assert "fastapi" in package_hints


def test_changes_rows_carry_paths_and_evidence_ids() -> None:
    _, changes, _, _, tables = collected()
    assert len(tables["changes"]) == len(changes.files)
    paths = {row[2] for row in tables["changes"]}
    assert "pyproject.toml" in paths
    assert all(row[0].startswith("ev_file_") for row in tables["changes"])


def test_bad_commit_log_error_has_a_file_hint() -> None:
    runs, changes, job_logs, deps, tables = collected("bad_commit")
    file_hints = {row[6] for row in tables["log_errors"]}
    assert "redrun/collectors/github_runs.py" in file_hints


def test_dependencies_table_reflects_the_collector() -> None:
    _, _, _, deps, tables = collected()
    assert len(tables["dependencies"]) == len(deps.dependencies)
    by_package = {row[1]: row for row in tables["dependencies"]}
    assert by_package["fastapi"][5] is True
    assert by_package["httpx"][5] is False


def test_booleans_survive_the_parquet_round_trip(tmp_path: Path) -> None:
    _, _, _, _, tables = collected()
    write_tables("t4", tables, tmp_path)
    con = duckdb.connect()
    value = con.execute(
        f"SELECT released_between_runs FROM read_parquet('{tmp_path}/t4/dependencies.parquet') "
        "WHERE package = 'fastapi'"
    ).fetchone()
    assert value == (True,)


def test_empty_tables_still_write_a_schema(tmp_path: Path) -> None:
    _, _, _, _, tables = collected("bad_commit")
    paths = write_tables("t5", tables, tmp_path)
    con = duckdb.connect()
    count = con.execute(f"SELECT count(*) FROM read_parquet('{paths['dependencies']}')").fetchone()
    assert count == (0,)
