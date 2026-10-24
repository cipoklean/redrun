"""SSE: events must arrive live, in order, and replay from a finished trace."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from redrun.api.app import create_app
from redrun.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        sample_mode=True,
        samples_dir=SAMPLES,
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )
    return TestClient(create_app(settings))


def test_stream_emits_events_in_pipeline_order(client: TestClient) -> None:
    with client.stream("POST", "/run/stream", json={"sample": "bad_commit"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payloads = [line for line in response.iter_lines() if line.startswith("data: ")]

    events = [json.loads(line[len("data: "):]) for line in payloads]
    assert events, "no events streamed"
    assert events[0]["agent"] == "triage"
    assert events[0]["type"] == "start"

    agents = [event["agent"] for event in events]
    assert "collector" in agents
    assert "zetaris" in agents
    assert "investigator" in agents
    assert "skeptic" in agents

    types = [event["type"] for event in events]
    assert "handoff" in types
    assert types[-1] == "result"


def test_stream_carries_step_and_token_data(client: TestClient) -> None:
    with client.stream("POST", "/run/stream", json={"sample": "bad_commit"}) as response:
        payloads = [line for line in response.iter_lines() if line.startswith("data: ")]

    events = [json.loads(line[len("data: "):]) for line in payloads]
    llm_events = [
        event
        for event in events
        if event["agent"] == "investigator" and event["type"] == "progress"
    ]
    assert llm_events
    first = llm_events[0]["data"]
    for key in ("provider", "model", "lane", "latency_ms", "tokens_in", "tokens_out", "step"):
        assert key in first, key


def test_stream_never_leaks_raw_logs_or_secrets(client: TestClient) -> None:
    with client.stream("POST", "/run/stream", json={"sample": "bad_commit"}) as response:
        body = "".join(line for line in response.iter_lines())
    assert "ghp_" not in body
    assert "Traceback (most recent call last)" not in body


def test_stream_reports_errors_as_an_error_event(client: TestClient) -> None:
    with client.stream("POST", "/run/stream", json={}) as response:
        body = [line for line in response.iter_lines() if line.startswith("event: error")]
    assert body


def test_finished_run_replays_from_its_trace(client: TestClient) -> None:
    result = client.post("/run", json={"sample": "bad_commit"}).json()
    trace_id = result["trace_id"]

    with client.stream("GET", f"/run/{trace_id}/events") as response:
        assert response.status_code == 200
        payloads = [line for line in response.iter_lines() if line.startswith("data: ")]

    events = [json.loads(line[len("data: "):]) for line in payloads]
    assert events[0]["agent"] == "triage"
    assert events[-1]["type"] == "result"


def test_unknown_trace_is_a_404(client: TestClient) -> None:
    assert client.get("/run/doesnotexist/events").status_code == 404
