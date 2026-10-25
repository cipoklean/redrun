"""The eval runner, end to end and offline, over copies of the bundled samples.

These tests are the guard on the claim "the eval replays offline": if replay ever
starts needing the network, or the report stops being generated, this fails.
"""

import json
import shutil
from pathlib import Path

import pytest

from redrun.config import Settings
from redrun.core.taxonomy import Cause
from redrun.eval import runner
from redrun.eval.cases import Case, CaseSet

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261025


def _baseline(reply_cause: str) -> dict:
    return {
        "recorded_at": "2026-10-25T00:00:00",
        "lane": "fast",
        "provider": "nvidia",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "prompt_lines": 300,
        "reply": {"cause": reply_cause, "root_cause": "the log names the test", "next_step": "rerun"},
        "raw": '{"cause": "%s"}' % reply_cause,
        "usage": {"prompt_tokens": 900, "completion_tokens": 40, "latency_ms": 1500},
        "note": "test fixture",
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Settings:
    samples = tmp_path / "samples"
    samples.mkdir()
    shutil.copytree(ROOT / "samples" / "bad_commit", samples / "eval_bad_commit")
    shutil.copytree(ROOT / "samples" / "flaky_test", samples / "eval_flaky_test")
    (samples / "eval_bad_commit" / "baseline.json").write_text(
        json.dumps(_baseline("bad_commit")), encoding="utf-8"
    )
    return Settings(
        sample_mode=True,
        samples_dir=samples,
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )


def _case_set() -> CaseSet:
    return CaseSet(
        seed=SEED,
        cases=(
            Case(
                id="bad_commit",
                url="https://github.com/example/lab/actions/runs/1",
                expected_cause=Cause.BAD_COMMIT,
                root_cause_keywords=("early-return", "created_at"),
                split="dev",
                constructed=True,
                notes="a copied sample, used as a case",
            ),
            Case(
                id="flaky_test",
                url="https://github.com/example/lab/actions/runs/2",
                expected_cause=Cause.FLAKY_TEST,
                root_cause_keywords=("flaky", "commit"),
                split="holdout",
                constructed=False,
                notes="the same, as a holdout case",
            ),
        ),
    )


def test_replay_scores_both_systems_and_writes_both_files(workspace: Settings, tmp_path: Path):
    payload = runner.run(
        _case_set(),
        split="all",
        mode="replay",
        settings=workspace,
        with_baseline=True,
        out_dir=tmp_path / "results",
        markdown_path=tmp_path / "RESULTS.md",
    )

    rows = {row["id"]: row for row in payload["rows"]}
    assert rows["bad_commit"]["agent"]["cause"] == "bad_commit"
    assert rows["bad_commit"]["agent"]["category_ok"] is True
    assert rows["bad_commit"]["agent"]["keywords_ok"] is True
    assert rows["flaky_test"]["agent"]["cause"] == "flaky_test"
    assert payload["summaries"]["all"]["agent"]["cause_hits"] == 2
    assert payload["summaries"]["dev"]["agent"]["cases"] == 1
    assert payload["summaries"]["holdout"]["agent"]["cases"] == 1
    assert payload["pending"] == []

    assert rows["bad_commit"]["baseline"]["cause"] == "bad_commit"
    assert rows["flaky_test"]["baseline"] is None
    assert [item["id"] for item in payload["baseline_pending"]] == ["flaky_test"]

    written = list((tmp_path / "results").glob("*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["meta"]["seed"] == SEED
    assert (tmp_path / "RESULTS.md").exists()
    assert list((tmp_path / "results").glob("*.md")), "the markdown is kept beside the json"


def test_the_report_lists_dev_and_holdout_separately(workspace: Settings, tmp_path: Path):
    payload = runner.run(
        _case_set(),
        split="all",
        mode="replay",
        settings=workspace,
        with_baseline=True,
        out_dir=tmp_path / "results",
        markdown_path=tmp_path / "RESULTS.md",
    )
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "`replay`" in markdown
    assert "| dev |" in markdown and "| holdout |" in markdown and "| all |" in markdown
    assert "| bad_commit | bad_commit | dev | yes |" in markdown
    assert "| flaky_test | flaky_test | holdout | no |" in markdown
    assert "not recorded" in markdown
    assert payload["meta"]["mode"] == "replay"


def test_a_split_run_only_touches_that_split(workspace: Settings, tmp_path: Path):
    payload = runner.run(
        _case_set(),
        split="holdout",
        mode="replay",
        settings=workspace,
        with_baseline=False,
        out_dir=tmp_path / "results",
        markdown_path=tmp_path / "RESULTS.md",
    )
    assert [row["id"] for row in payload["rows"]] == ["flaky_test"]
    assert payload["meta"]["case_count"] == 1


def test_an_unrecorded_llm_is_reported_as_pending_not_scored(workspace: Settings, tmp_path: Path):
    """A sample without model replies is a pending case, and never a fake verdict."""
    sample = workspace.samples_dir / "eval_bad_commit"
    (sample / "llm" / "steps.json").write_text("[]\n", encoding="utf-8")
    for leftover in ("fast.json", "deep.json"):
        (sample / "llm" / leftover).unlink()

    payload = runner.run(
        CaseSet(seed=SEED, cases=(_case_set().by_id("bad_commit"),)),
        split="dev",
        mode="replay",
        settings=workspace,
        with_baseline=True,
        out_dir=tmp_path / "results",
        markdown_path=tmp_path / "RESULTS.md",
    )
    row = payload["rows"][0]
    assert row["agent"] is None
    assert row["path"] == "rules_pending_llm"
    assert "no recorded LLM step" in row["path_reason"]
    assert payload["summaries"]["dev"]["agent"]["cases"] == 0
    assert payload["pending"][0]["id"] == "bad_commit"
    assert payload["pending"][0]["rules_cause"], "the rules path is reported as a diagnostic only"
    assert "pending (rules_pending_llm)" in (tmp_path / "RESULTS.md").read_text(encoding="utf-8")


def test_the_report_names_the_provider_behind_each_system(workspace: Settings, tmp_path: Path):
    """A Nightshift recording and a direct one are different evidence, so say which."""
    payload = runner.run(
        _case_set(),
        split="all",
        mode="replay",
        settings=workspace,
        with_baseline=True,
        out_dir=tmp_path / "results",
        markdown_path=tmp_path / "RESULTS.md",
    )
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")

    assert "agent provider" in markdown
    assert "baseline provider" in markdown
    assert "recordings served by" in markdown
    # The copied samples predate the recorder keeping the provider, so the report says
    # that rather than naming one.
    assert "not recorded" in markdown
    assert payload["summaries"]["all"]["agent"]["providers"] == []


def test_a_missing_case_file_is_an_error_not_an_empty_run(tmp_path: Path):
    from redrun.eval import cases as cases_module

    with pytest.raises(cases_module.CaseError):
        cases_module.load(tmp_path / "nope.yaml")
