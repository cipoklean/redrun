"""The API surface: POST /run, GET /health, GET /provider/status."""

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


def test_health_reports_sample_mode(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["sample_mode"] is True


def test_provider_status_in_sample_mode(client: TestClient) -> None:
    body = client.get("/provider/status").json()
    assert body["llm"] == {"provider": "sample", "status": "sample"}
    assert body["zetaris"]["configured"] is False
    assert body["zetaris"]["status"] == "offline"


def test_run_reports_links_and_the_query_engine(client: TestClient) -> None:
    body = client.post("/run", json={"sample": "dependency_bump"}).json()
    assert body["metrics"]["query_engine"] == "zetaris_replay"
    assert body["links"]
    assert body["links"][0]["link_type"] == "dependency_match"


def test_run_by_sample_returns_the_contract(client: TestClient) -> None:
    response = client.post("/run", json={"sample": "dependency_bump"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "run_id",
        "input",
        "failed_run",
        "last_green_run",
        "evidence",
        "verdict",
        "links",
        "skeptic",
        "trace_id",
        "metrics",
    }
    assert body["verdict"]["cause"] == "dependency_bump"
    assert body["input"] == "sample:dependency_bump"


def test_run_keeps_the_target_as_input(client: TestClient) -> None:
    body = client.post("/run", json={"target": "acme/widgets", "sample": "bad_commit"}).json()
    assert body["input"] == "acme/widgets"
    assert body["verdict"]["cause"] == "bad_commit"


def test_run_writes_a_trace_file(client: TestClient, tmp_path: Path) -> None:
    body = client.post("/run", json={"sample": "expired_or_missing_secret"}).json()
    trace = tmp_path / "logs" / f"{body['trace_id']}.jsonl"
    assert trace.exists()
    assert len(trace.read_text().strip().splitlines()) >= 4


def test_run_with_no_target_and_no_sample_is_a_client_error(client: TestClient) -> None:
    response = client.post("/run", json={})
    assert response.status_code == 400
    assert "target" in response.json()["detail"].lower()


def test_run_rejects_a_bad_target(client: TestClient) -> None:
    response = client.post("/run", json={"target": "not a repo"})
    assert response.status_code == 400


def test_run_rejects_an_unknown_sample(client: TestClient) -> None:
    response = client.post("/run", json={"sample": "nope"})
    assert response.status_code == 502
    assert "fixture" in response.json()["detail"].lower()
