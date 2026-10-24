"""The endpoints the frontend needs: samples, a stored result, a provider test."""

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
        frontend_dir=tmp_path / "dist",
    )
    return TestClient(create_app(settings))


def test_samples_lists_every_case_with_its_category(client: TestClient) -> None:
    body = client.get("/samples").json()
    names = [item["name"] for item in body["samples"]]
    assert names == ["dependency_bump", "bad_commit", "expired_or_missing_secret", "flaky_test"]
    categories = {item["name"]: item["category"] for item in body["samples"]}
    assert categories["flaky_test"] == "flaky_test"
    assert all(item["description"] for item in body["samples"])


def test_a_finished_run_can_be_read_back(client: TestClient) -> None:
    result = client.post("/run", json={"sample": "flaky_test"}).json()

    stored = client.get(f"/run/{result['trace_id']}")
    assert stored.status_code == 200
    assert stored.headers["content-type"].startswith("application/json")
    body = stored.json()
    assert body["verdict"]["cause"] == "flaky_test"
    assert body["skeptic"]["revisions"] == 1
    assert body["verdict"]["cited_evidence_ids"]


def test_an_unknown_run_is_a_404_not_an_empty_result(client: TestClient) -> None:
    response = client.get("/run/nosuchtrace")
    assert response.status_code == 404
    assert "no stored result" in response.json()["detail"]


def test_a_browser_navigation_gets_the_page_not_the_json(client: TestClient, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>RedRun</title>", encoding="utf-8")

    page = client.get("/run/anything", headers={"accept": "text/html,application/xhtml+xml"})
    assert page.status_code == 200
    assert "RedRun" in page.text


def test_the_spa_falls_back_for_client_routes(client: TestClient, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>RedRun</title>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")

    assert "RedRun" in client.get("/settings", headers={"accept": "text/html"}).text
    assert "RedRun" in client.get("/run/abc/verdict", headers={"accept": "text/html"}).text
    assert client.get("/favicon.svg").text == "<svg/>"


def test_api_paths_are_not_swallowed_by_the_spa(client: TestClient, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>RedRun</title>", encoding="utf-8")

    assert client.get("/health", headers={"accept": "text/html"}).json()["status"] == "ok"
    assert "samples" in client.get("/samples", headers={"accept": "text/html"}).json()


def test_provider_status_never_returns_a_token(client: TestClient) -> None:
    body = client.get("/provider/status").json()
    text = json.dumps(body)
    assert "token_set" in body["github"]
    assert body["llm"]["token_set"] is False
    assert "meterless_runtime_token" not in text
    assert set(body["llm"]["models"]) == {"fast", "deep", "judge"}


def test_provider_test_reports_status_without_secrets(client: TestClient) -> None:
    body = client.get("/provider/test").json()
    assert body["llm"]["status"] in {"online", "unauthorized", "offline", "sample"}
    assert isinstance(body["llm"]["model_count"], int)
    assert body["zetaris"]["status"] in {"online", "unauthorized", "offline"}
    assert body["summary"]
    text = json.dumps(body)
    for forbidden in ("password", "runtime_token", "api_key"):
        assert forbidden not in text


def test_health_reports_whether_the_ui_is_built(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ui"] is False
