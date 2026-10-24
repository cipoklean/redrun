"""Normalise collector output into four tables, written as Parquet and CSV."""

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs as logs_collector
from redrun.core.hints import all_file_hints, error_class, package_hint

TABLE_NAMES = ("runs", "changes", "log_errors", "dependencies")

SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "runs": [
        ("run_id", "BIGINT"),
        ("role", "VARCHAR"),
        ("sha", "VARCHAR"),
        ("started_at", "VARCHAR"),
        ("conclusion", "VARCHAR"),
        ("runner_image", "VARCHAR"),
        # GitHub's run object does not carry this; resolving it needs a tree/blob
        # lookup at the ref. Left NULL rather than invented. See TODO in the report.
        ("workflow_file_sha", "VARCHAR"),
    ],
    "changes": [
        ("evidence_id", "VARCHAR"),
        ("sha", "VARCHAR"),
        ("path", "VARCHAR"),
        ("change_type", "VARCHAR"),
        ("package_hint", "VARCHAR"),
        ("committed_at", "VARCHAR"),
    ],
    "log_errors": [
        ("evidence_id", "VARCHAR"),
        ("job", "VARCHAR"),
        ("step", "VARCHAR"),
        ("line_no", "BIGINT"),
        ("error_class", "VARCHAR"),
        ("message", "VARCHAR"),
        ("file_hint", "VARCHAR"),
        ("package_hint", "VARCHAR"),
    ],
    "dependencies": [
        ("evidence_id", "VARCHAR"),
        ("package", "VARCHAR"),
        ("old_version", "VARCHAR"),
        ("new_version", "VARCHAR"),
        ("released_at", "VARCHAR"),
        ("released_between_runs", "BOOLEAN"),
    ],
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _runs_rows(runs: github_runs.RunsCollected) -> list[tuple]:
    rows: list[tuple] = []
    for role, run in (("green", runs.last_green_run), ("failed", runs.failed_run)):
        if run is None:
            continue
        rows.append(
            (
                run.run_id,
                role,
                run.head_sha,
                run.started_at.isoformat() if hasattr(run, "started_at") else run.created_at.isoformat(),
                run.conclusion,
                run.runner_image,
                None,
            )
        )
    return rows


def _changes_rows(
    runs: github_runs.RunsCollected, changes: diff_collector.DiffCollected
) -> list[tuple]:
    committed = max((c.date for c in changes.commits if c.date), default=runs.failed_run.created_at)
    rows: list[tuple] = []
    for item in changes.evidence:
        if item.kind != "file":
            continue
        filename = str(item.metadata.get("filename", ""))
        parsed = deps_collector.parse_manifest_lines(item.excerpt.splitlines())
        rows.append(
            (
                item.id,
                runs.failed_run.head_sha,
                filename,
                str(item.metadata.get("status", "modified")),
                sorted(parsed)[0] if parsed else None,
                _iso(committed),
            )
        )
    return rows


def _single_failing_step(run_has_steps: list[str]) -> str | None:
    return run_has_steps[0] if len(run_has_steps) == 1 else None


def _log_error_rows(
    runs: github_runs.RunsCollected,
    job_logs: logs_collector.LogsCollected,
    changed_paths: set[str],
) -> list[tuple]:
    windows = {e.id: e.excerpt for e in job_logs.evidence if e.kind == "log"}
    failing_steps = {
        job.name: [s.name for s in job.steps if s.conclusion not in ("success", "skipped", "neutral")]
        for job in runs.failed_run.jobs
    }
    rows: list[tuple] = []
    for match in job_logs.matches:
        context = windows.get(match.evidence_id, "")
        candidates: list[str] = []
        for source in (match.text, context):
            for path in all_file_hints(source):
                if path not in candidates:
                    candidates.append(path)
        # The changed file is the interesting one: a pytest FAILED line names the test
        # file, while the frame below it names the file that actually changed.
        preferred = next((p for p in candidates if p in changed_paths), candidates[0] if candidates else None)
        rows.append(
            (
                match.evidence_id,
                match.job,
                _single_failing_step(failing_steps.get(match.job, [])),
                match.line,
                error_class(match.text) or match.pattern,
                match.text,
                preferred,
                package_hint(match.text) or package_hint(context),
            )
        )
    return rows


def _dependency_rows(deps: deps_collector.DepsCollected) -> list[tuple]:
    return [
        (
            record.evidence_id,
            record.package,
            record.old_version,
            record.new_version,
            _iso(record.released_at),
            record.released_between_runs,
        )
        for record in deps.dependencies
    ]


def build_tables(
    runs: github_runs.RunsCollected,
    changes: diff_collector.DiffCollected,
    job_logs: logs_collector.LogsCollected,
    deps: deps_collector.DepsCollected,
) -> dict[str, list[tuple]]:
    return {
        "runs": _runs_rows(runs),
        "changes": _changes_rows(runs, changes),
        "log_errors": _log_error_rows(runs, job_logs, {c.filename for c in changes.files}),
        "dependencies": _dependency_rows(deps),
    }


def write_tables(
    trace_id: str, tables: dict[str, list[tuple]], data_dir: Path
) -> dict[str, Path]:
    """Write every table as Parquet (for the engines) and CSV (for a human)."""
    out_dir = Path(data_dir) / trace_id
    out_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    paths: dict[str, Path] = {}
    try:
        for name in TABLE_NAMES:
            columns = SCHEMAS[name]
            connection.execute(
                f"CREATE TABLE {name} ({', '.join(f'{col} {kind}' for col, kind in columns)})"
            )
            rows: list[Any] = tables.get(name) or []
            if rows:
                placeholders = ", ".join("?" for _ in columns)
                connection.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)

            parquet = out_dir / f"{name}.parquet"
            csv_path = out_dir / f"{name}.csv"
            connection.execute(
                f"COPY {name} TO '{str(parquet).replace(chr(39), chr(39) * 2)}' (FORMAT PARQUET)"
            )
            connection.execute(
                f"COPY {name} TO '{str(csv_path).replace(chr(39), chr(39) * 2)}' "
                "(HEADER, FORMAT CSV)"
            )
            paths[name] = parquet
    finally:
        connection.close()
    return paths
