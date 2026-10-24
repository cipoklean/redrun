"""Local fallback engine: the same overlap.sql, run on the Parquet files with DuckDB."""

import hashlib
import time
from pathlib import Path

import duckdb

from redrun.adapters.base import QueryFailed, QueryResult


class DuckDBEngine:
    engine_id = "duckdb"

    def __init__(self) -> None:
        self._connection = None
        self.method = ""

    def register_sources(self, trace_id: str, table_paths: dict[str, Path]) -> str:
        connection = duckdb.connect()
        for name, path in table_paths.items():
            target = str(path).replace("'", "''")
            connection.execute(
                f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{target}')"
            )
        self._connection = connection
        self.method = "in_place_parquet"
        return self.method

    def query(self, sql: str) -> QueryResult:
        if self._connection is None:
            raise QueryFailed("register_sources must be called before query")
        started = time.perf_counter()
        try:
            cursor = self._connection.execute(sql)
            columns = [description[0] for description in cursor.description] if cursor.description else []
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
        except duckdb.Error as exc:
            raise QueryFailed(f"duckdb: {exc}") from exc
        return QueryResult(
            rows=rows,
            row_count=len(rows),
            latency_ms=int((time.perf_counter() - started) * 1000),
            sql_sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
