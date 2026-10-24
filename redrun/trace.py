"""JSONL trace writer: one Event per line, redacted on the way out."""

from pathlib import Path
from typing import Any, Callable

from redrun.core.models import AgentName, Event, EventType
from redrun.core.redact import redact, redact_data


class Tracer:
    """Append-only trace for one run. `logs/<trace_id>.jsonl`."""

    def __init__(
        self,
        trace_id: str,
        logs_dir: Path,
        sink: Callable[[Event], None] | None = None,
    ):
        self.trace_id = trace_id
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.logs_dir / f"{trace_id}.jsonl"
        self.sink = sink

    def emit(
        self,
        agent: AgentName,
        event_type: EventType,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> Event:
        cleaned = redact_data(data or {})
        event = Event(
            agent=agent,
            type=event_type,
            message=redact(message),
            data=cleaned if isinstance(cleaned, dict) else {"value": cleaned},
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
        if self.sink is not None:
            self.sink(event)
        return event
