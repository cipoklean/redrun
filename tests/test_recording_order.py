"""The recorder's order of operations: the sample is written before the client closes.

`write_sample` re-reads the skeptic's live tool checks to record them, so a recorder
that closes the GitHub client first leaves every recording ending in a RuntimeError
from httpx. That is a live-only path, so this test stands in for the collectors and
asserts the ordering directly.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from redrun.cli import record_sample
from redrun.config import Settings
from redrun.pipeline import Prepared


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.closed_at: str | None = None

    def close(self) -> None:
        self.closed = True
        self.closed_at = "after"


@pytest.fixture()
def wired(monkeypatch, tmp_path: Path):
    """A prepared run whose collectors and model are stubs, and a spy for write_sample."""
    from redrun.adapters.llm import LLMError

    client = FakeClient()
    prepared = Prepared(
        trace_id="abcd1234",
        tracer=object(),
        sample=None,
        runs=SimpleNamespace(failed_run=None, last_green_run=None),
        changes=object(),
        job_logs=object(),
        deps=object(),
        evidence=[],
        table_paths={},
        client=client,
    )
    calls: dict[str, object] = {}

    monkeypatch.setattr("redrun.cli.prepare", lambda *a, **k: prepared)
    monkeypatch.setattr("redrun.cli.run_overlap", lambda *a, **k: ([], "duckdb", 0))
    monkeypatch.setattr("redrun.cli.ingest", lambda **k: object())
    monkeypatch.setattr("redrun.cli.rule_verdict", lambda *a, **k: _verdict())
    monkeypatch.setattr("redrun.cli._triage_cause", lambda links, logs: None)

    class NoModel:
        """An LLM client whose calls fail, exactly as a 500 or a 503 does."""

        def complete(self, messages, lane="fast"):
            raise LLMError("no model here")

    monkeypatch.setattr("redrun.cli.LLMClient", lambda *a, **k: NoModel())

    class StubInvestigator:
        """Fails where the real one fails: on its first model call."""

        def __init__(self, **kwargs):
            self.llm = kwargs["llm"]

        def run(self, triage_cause=None):
            self.llm.complete([{"role": "user", "content": "chunk"}])
            raise AssertionError("the stub model should have raised")

    monkeypatch.setattr("redrun.cli.MarkovianInvestigator", StubInvestigator)

    class StubSkeptic:
        def __init__(self, **kwargs):
            calls["skeptic_github"] = kwargs.get("github")

        def check(self, verdict):
            calls["checked_cause"] = verdict.cause.value

    monkeypatch.setattr("redrun.cli.Skeptic", StubSkeptic)

    def spy_write_sample(**kwargs):
        calls["client_closed_when_written"] = client.closed
        calls["skeptic"] = kwargs.get("skeptic")
        target = tmp_path / kwargs["name"]
        (target / "llm").mkdir(parents=True)
        return target

    monkeypatch.setattr("redrun.cli.write_sample", spy_write_sample)
    return client, calls, tmp_path


def _verdict():
    from redrun.core.models import Verdict
    from redrun.core.taxonomy import Cause

    return Verdict(
        cause=Cause.BAD_COMMIT,
        root_cause="a stub",
        confidence=0.2,
        confidence_breakdown=[],
        next_step="look",
        cited_evidence_ids=[],
    )


def test_the_sample_is_written_before_the_client_is_closed(wired) -> None:
    client, calls, tmp_path = wired
    settings = Settings(sample_mode=False, samples_dir=tmp_path, logs_dir=tmp_path / "logs")

    code = record_sample("https://github.com/owner/repo/actions/runs/1", "eval_x", settings)

    assert code == 0
    assert calls["client_closed_when_written"] is False, (
        "write_sample needs the client to read the skeptic's live tool checks"
    )
    assert client.closed is True, "the client is closed once the sample is on disk"
    assert calls["skeptic"] is not None
    assert calls["skeptic_github"] is client


def test_a_model_that_cannot_answer_still_records_the_collectors(wired, monkeypatch) -> None:
    client, calls, tmp_path = wired
    settings = Settings(sample_mode=False, samples_dir=tmp_path, logs_dir=tmp_path / "logs")

    code = record_sample("https://github.com/owner/repo/actions/runs/1", "eval_x", settings)

    assert code == 0
    pending = tmp_path / "eval_x" / "llm" / "pending.json"
    assert pending.exists(), "the model half is marked pending rather than left ambiguous"
    body = pending.read_text(encoding="utf-8")
    assert "no model here" in body
    assert "replayable" in body
    assert not (tmp_path / "eval_x" / "llm" / "steps.json").exists()
