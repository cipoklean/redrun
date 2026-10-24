"""Events must carry token counts; those are metrics, not secrets."""

import json
from pathlib import Path

from redrun.config import Settings
from redrun.core.models import Event
from redrun.core.redact import redact
from redrun.pipeline import investigate

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def test_token_counts_survive_into_the_trace(tmp_path: Path) -> None:
    settings = Settings(
        sample_mode=True, samples_dir=SAMPLES, logs_dir=tmp_path / "logs", data_dir=tmp_path / "data"
    )
    result = investigate(None, sample="dependency_bump", settings=settings)
    events = [
        Event.model_validate(json.loads(line))
        for line in (tmp_path / "logs" / f"{result.trace_id}.jsonl").read_text().splitlines()
    ]
    progress = [
        event for event in events if event.agent == "investigator" and event.type == "progress"
    ]
    assert progress
    for event in progress:
        assert "[REDACTED]" not in event.message, event.message
        assert event.data["tokens_in"] > 0
        assert event.data["tokens_out"] > 0
        assert str(event.data["tokens_in"]) in event.message


def test_a_token_shaped_key_is_still_redacted() -> None:
    """The message avoids KEY=value shapes; the redactor stays aggressive."""
    assert "3928" not in redact("TOKENS_IN=3928")
    assert "abc123" not in redact("GITHUB_TOKEN=abc123")
