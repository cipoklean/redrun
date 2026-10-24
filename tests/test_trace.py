"""Every run leaves a redacted JSONL trace, one Event per line."""

import json
from pathlib import Path

from redrun.core.models import Event
from redrun.trace import Tracer


def test_tracer_writes_one_event_per_line(tmp_path: Path) -> None:
    tracer = Tracer("trace123", tmp_path)
    tracer.emit("triage", "start", "parsing target", {"target": "acme/widgets"})
    tracer.emit("investigator", "result", "cause=dependency_bump", {"confidence": 0.8})

    path = tmp_path / "trace123.jsonl"
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    events = [Event.model_validate(json.loads(line)) for line in lines]
    assert [e.agent for e in events] == ["triage", "investigator"]
    assert [e.type for e in events] == ["start", "result"]
    assert events[1].data["confidence"] == 0.8


def test_tracer_redacts_messages_and_data(tmp_path: Path) -> None:
    tracer = Tracer("t2", tmp_path)
    token = "ghp_FAKEfakeFAKEfake1234FAKEfake1234"
    tracer.emit("collector", "progress", f"using {token}", {"header": f"Bearer {token}"})

    raw = (tmp_path / "t2.jsonl").read_text()
    assert token not in raw
    assert "[REDACTED]" in raw
    assert json.loads(raw)["agent"] == "collector"


def test_tracer_creates_the_logs_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "logs"
    Tracer("t3", target).emit("triage", "start", "hello")
    assert (target / "t3.jsonl").exists()
