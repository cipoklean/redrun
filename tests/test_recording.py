"""Recording a sample must produce a directory that replays end to end."""

import json
from pathlib import Path

from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Link
from redrun.pipeline import investigate, prepare
from redrun.recording import RecordingLLM, write_sample

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def test_write_sample_produces_a_replayable_directory(tmp_path: Path) -> None:
    source = Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=tmp_path / "logs", data_dir=tmp_path / "data")
    prepared = prepare(None, "bad_commit", source)
    links = [
        Link(**row)
        for row in json.loads((SAMPLES / "bad_commit" / "zetaris" / "overlap.json").read_text())["rows"]
    ]
    steps = json.loads((SAMPLES / "bad_commit" / "llm" / "steps.json").read_text())

    recorded = Settings(
        sample_mode=True,
        samples_dir=tmp_path / "samples",
        logs_dir=tmp_path / "logs2",
        data_dir=tmp_path / "data2",
    )
    directory = write_sample(
        settings=recorded,
        name="recorded_case",
        prepared=prepared,
        links=links,
        engine_label="duckdb",
        llm_steps=[{"lane": "fast", "text": step["text"], "usage": step["usage"]} for step in steps],
    )

    for relative in (
        "run.json",
        "diff.json",
        "logs.json",
        "pypi.json",
        "zetaris/overlap.json",
        "skeptic/same_sha.json",
        "skeptic/history.json",
        "skeptic/outage.json",
        "llm/steps.json",
        "world.json",
    ):
        assert (directory / relative).exists(), relative

    overlap = json.loads((directory / "zetaris" / "overlap.json").read_text())
    assert overlap["recorded_from"] == "duckdb"

    # The real test: replay it through the pipeline.
    result = investigate(None, sample="recorded_case", settings=recorded)
    assert result.verdict is not None
    assert result.verdict.cause.value == "bad_commit"
    assert result.metrics.query_engine == "zetaris_replay"
    assert result.skeptic is not None


def test_recording_llm_captures_every_step() -> None:
    class Inner:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, lane="fast"):
            from redrun.adapters.llm import Completion, Usage

            self.calls += 1
            return Completion(
                text=f"reply {self.calls}",
                usage=Usage(
                    prompt_tokens=10,
                    completion_tokens=2,
                    latency_ms=3,
                    provider="fake",
                    model="fake/model",
                ),
            )

    recorder = RecordingLLM(Inner())
    recorder.complete([{"role": "user", "content": "a"}], lane="fast")
    recorder.complete([{"role": "user", "content": "b"}], lane="deep")

    assert [step["text"] for step in recorder.steps] == ["reply 1", "reply 2"]
    assert [step["lane"] for step in recorder.steps] == ["fast", "deep"]
    assert recorder.steps[0]["usage"]["prompt_tokens"] == 10


def test_recording_refuses_in_sample_mode(tmp_path: Path, capsys) -> None:
    from redrun.cli import record_sample

    base = Settings(sample_mode=True, samples_dir=SAMPLES)
    assert record_sample("https://github.com/acme/widgets/actions/runs/1", "x", base) == 1
    assert "SAMPLE_MODE=false" in capsys.readouterr().out
