"""Shared engine surface: one result shape, one configuration error."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class EngineError(RuntimeError):
    """Base for engine problems the pipeline turns into a fallback or an error event."""


class NotConfigured(EngineError):
    """Credentials or a confirmed endpoint are missing. Never faked as success."""


class QueryFailed(EngineError):
    """The engine was reachable but the statement failed."""


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict]
    row_count: int
    latency_ms: int
    sql_sha256: str


class QueryEngine(Protocol):
    engine_id: str

    def register_sources(self, trace_id: str, table_paths: dict[str, Path]) -> str:
        """Make the four tables queryable. Returns the method used, for the trace."""
        ...

    def query(self, sql: str) -> QueryResult: ...

    def close(self) -> None: ...
