"""CLI: `python -m redrun investigate ...` must work offline from a clean clone."""

import json
from pathlib import Path

import pytest

from redrun.cli import main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    logs = tmp_path / "logs"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("REDRUN_LOGS_DIR", str(logs))
    monkeypatch.setenv("REDRUN_DATA_DIR", str(tmp_path / "data"))
    return logs


def test_json_output_is_a_valid_run_result(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["investigate", "--sample", "dependency_bump", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"]["cause"] == "dependency_bump"
    assert payload["run_id"] == "5001"


def test_sample_can_be_combined_with_a_target(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "acme/widgets", "--sample", "bad_commit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["input"] == "acme/widgets"


def test_human_output_names_the_cause_and_next_step(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "--sample", "expired_or_missing_secret"]) == 0
    out = capsys.readouterr().out
    assert "expired_or_missing_secret" in out
    assert "Next step:" in out
    assert "confidence" in out.lower()


def test_trace_is_written_to_the_configured_logs_dir(
    capsys: pytest.CaptureFixture[str], isolated_logs: Path
) -> None:
    assert main(["investigate", "--sample", "bad_commit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (isolated_logs / f"{payload['trace_id']}.jsonl").exists()


def test_bad_target_exits_nonzero_with_a_message(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "not a repo"]) == 2
    err = capsys.readouterr().err
    assert "GitHub run URL" in err or "owner/repo" in err


def test_no_arguments_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_engine_flag_forces_duckdb(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "--sample", "bad_commit", "--engine", "duckdb", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"]["query_engine"] == "duckdb"


def test_sample_defaults_to_the_recorded_engine(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "--sample", "bad_commit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"]["query_engine"] == "zetaris_replay"
    assert payload["links"]


def test_human_output_shows_links_and_engine(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "--sample", "dependency_bump"]) == 0
    out = capsys.readouterr().out
    assert "zetaris_replay" in out
    assert "dependency_match" in out


def test_zetaris_check_without_credentials_exits_nonzero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ZETARIS_URL", "ZETARIS_TOKEN", "ZETARIS_USER", "ZETARIS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert main(["zetaris-check"]) == 1
    out = capsys.readouterr().out
    assert "offline" in out
    assert "refusing" in out.lower()
