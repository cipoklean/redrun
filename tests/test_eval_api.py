"""The eval screen's endpoint: the newest results payload, or an honest 404."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from redrun.api.app import create_app
from redrun.config import Settings
from redrun.eval import runner

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

PAYLOAD = {
    "meta": {
        "timestamp": "2026-10-25T00:00:00Z",
        "mode": "replay",
        "split": "all",
        "seed": 20261025,
        "case_count": 1,
        "baseline_lines": 300,
        "baseline_model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "git_sha": "abc1234",
        "notes": "",
    },
    "rows": [],
    "summaries": {},
    "pending": [],
    "baseline_pending": [],
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """A client whose frontend is built in the test, not on the developer's disk.

    The first version of these tests pointed at the repository's `frontend/dist` and
    passed locally because a build happened to be sitting there. In a clean clone
    the shell route correctly answers 404 and the test failed for the wrong reason.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>RedRun</title>", encoding="utf-8")
    settings = Settings(
        sample_mode=True,
        samples_dir=SAMPLES,
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
        frontend_dir=dist,
    )
    return TestClient(create_app(settings))


def test_eval_reports_a_missing_run_instead_of_an_empty_table(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "nothing-here")
    response = client.get("/eval")
    assert response.status_code == 404
    assert "no evaluation results yet" in response.json()["detail"]


def test_eval_serves_the_newest_results(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "20261024T000000Z.json").write_text(
        json.dumps({**PAYLOAD, "meta": {**PAYLOAD["meta"], "mode": "old"}}), encoding="utf-8"
    )
    (results / "20261025T000000Z.json").write_text(json.dumps(PAYLOAD), encoding="utf-8")
    monkeypatch.setattr(runner, "RESULTS_DIR", results)

    body = client.get("/eval").json()
    assert body["meta"]["seed"] == 20261025
    assert body["meta"]["mode"] == "replay"


def test_eval_rejects_a_corrupt_result_file(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "20261025T000000Z.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(runner, "RESULTS_DIR", results)
    assert client.get("/eval").status_code == 500


def test_a_browser_asking_for_eval_gets_the_app_shell(client: TestClient) -> None:
    response = client.get("/eval", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text


def test_the_two_answers_to_one_url_vary_on_accept(client: TestClient) -> None:
    """One URL, two shapes, so each answer must declare that it did.

    Without `Vary: Accept`, a browser that loaded /eval as a document caches the
    shell and hands it to the page's own fetch, which then tries to parse HTML as
    JSON. The eval screen did exactly that before this header existed.
    """
    as_json = client.get("/eval", headers={"accept": "application/json"})
    assert as_json.status_code == 200
    assert as_json.headers["content-type"].startswith("application/json")
    assert as_json.headers["vary"].lower() == "accept"

    as_html = client.get("/eval", headers={"accept": "text/html"})
    assert as_html.status_code == 200
    assert as_html.headers["content-type"].startswith("text/html")
    assert as_html.headers["vary"].lower() == "accept"
