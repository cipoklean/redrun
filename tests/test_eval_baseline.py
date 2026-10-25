"""The baseline: one plain call, a narrow prompt, and no invented replies."""

import json

import httpx
import pytest

from redrun.config import Settings
from redrun.eval import baseline


def _completion(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40},
        },
    )


def test_the_prompt_carries_the_taxonomy_and_only_a_log_tail():
    log = "\n".join(f"line {number}" for number in range(1, 2001))
    messages = baseline.messages(log)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    for cause in baseline.causes():
        assert cause in messages[0]["content"]
    body = messages[1]["content"]
    lines = body.splitlines()
    assert lines[0] == "log tail:"
    assert len(lines) == baseline.BASELINE_LINES + 1
    assert lines[1] == "line 1701" and lines[-1] == "line 2000"


def test_parse_accepts_fenced_json_and_rejects_an_unknown_cause():
    payload = baseline.parse('```json\n{"cause": "bad_commit", "root_cause": "x", "next_step": "y"}\n```')
    assert payload["cause"] == "bad_commit" and payload["root_cause"] == "x"

    invented = baseline.parse('{"cause": "act_of_god", "root_cause": "x", "next_step": "y"}')
    assert invented["cause"] == baseline.INVALID

    assert baseline.parse("no json here")["cause"] == baseline.INVALID


def test_recording_needs_live_settings():
    with pytest.raises(baseline.BaselineError):
        baseline.call(Settings(sample_mode=True), "log")


def test_record_and_load_round_trip(tmp_path):
    transport = httpx.MockTransport(
        lambda request: _completion(
            '{"cause": "flaky_test", "root_cause": "the same sha passed", "next_step": "rerun"}'
        )
    )
    payload = baseline.record(
        Settings(sample_mode=False, llm_provider="nvidia", nvidia_api_key="k"),
        tmp_path,
        "log tail",
        transport=transport,
    )
    assert payload["reply"]["cause"] == "flaky_test"
    assert payload["usage"]["prompt_tokens"] == 900

    loaded = baseline.load(tmp_path)
    assert loaded is not None
    observation = baseline.observation(loaded)
    assert observation["cause"] == "flaky_test"
    assert observation["latency_ms"] >= 0
    assert observation["tokens_in"] == 900

    written = json.loads((tmp_path / baseline.BASELINE_FILE).read_text())
    assert written["note"], "a recorded baseline must say what the baseline was given"


def test_a_model_that_cannot_answer_is_an_error_not_an_empty_reply(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="nope"))
    with pytest.raises(baseline.BaselineError):
        baseline.record(
            Settings(sample_mode=False, llm_provider="nvidia", nvidia_api_key="k"),
            tmp_path,
            "log tail",
            transport=transport,
        )
    assert baseline.load(tmp_path) is None


def test_a_missing_record_is_none_not_a_guess(tmp_path):
    assert baseline.load(tmp_path) is None
    assert baseline.observation(None) is None


def test_tail_from_sample_reads_the_recorded_log(tmp_path):
    sample = tmp_path / "eval_case"
    sample.mkdir()
    (sample / "logs.json").write_text(
        json.dumps({"jobs": [{"job_id": 1, "name": "test", "log": "a\nb\nc\n"}]}),
        encoding="utf-8",
    )
    assert baseline.tail_from_sample(sample) == "a\nb\nc"
    assert baseline.tail_from_sample(tmp_path / "nothing") == ""
