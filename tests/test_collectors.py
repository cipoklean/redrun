"""Collectors must be deterministic, stable-ID'd, and redacted."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext, CollectorError
from redrun.config import Settings
from redrun.core.redact import REDACTED
from redrun.core.target import parse_target

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def settings(samples: Path = SAMPLES) -> Settings:
    return Settings(sample_mode=True, samples_dir=samples, logs_dir=ROOT / "logs")


def ctx(sample: str | None) -> CollectContext:
    return CollectContext(settings=settings(), sample=sample)


# ---------------------------------------------------------------- github_runs


@pytest.mark.parametrize("sample", ["dependency_bump", "bad_commit", "expired_or_missing_secret"])
def test_runs_collector_returns_both_runs(sample: str) -> None:
    got = github_runs.collect(ctx(sample))
    assert got.failed_run.run_id > 0
    assert got.last_green_run is not None
    assert got.failed_run.conclusion == "failure"
    assert got.last_green_run.conclusion == "success"
    assert got.last_green_run.created_at < got.failed_run.created_at


def test_runs_evidence_ids_are_stable_and_name_both_runs() -> None:
    got = github_runs.collect(ctx("dependency_bump"))
    ids = [e.id for e in got.evidence]
    assert ids == ["ev_run_5001", "ev_run_4980"]
    assert {e.kind for e in got.evidence} == {"run"}
    assert all(e.metadata["head_sha"] for e in got.evidence)
    assert got.evidence[0].metadata["runner_image"] == "ubuntu-24.04"


def test_runs_evidence_captures_the_failing_step() -> None:
    got = github_runs.collect(ctx("bad_commit"))
    failed = got.evidence[0]
    assert "Run pytest" in failed.excerpt
    assert failed.metadata["failing_steps"] == ["Run pytest"]


class FakeGitHub:
    """Records calls and serves canned runs, so selection logic is testable."""

    def __init__(self, runs: list[dict[str, Any]], jobs: list[dict[str, Any]] | None = None):
        self.runs = runs
        self.jobs = jobs or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_default_branch(self, owner: str, repo: str) -> str:
        self.calls.append(("get_default_branch", {"owner": owner, "repo": repo}))
        return "main"

    def get_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        self.calls.append(("get_run", {"run_id": run_id}))
        for run in self.runs:
            if run["id"] == run_id:
                return run
        raise CollectorError(f"no run {run_id}")

    def list_workflow_runs(self, owner: str, repo: str, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append(("list_workflow_runs", kw))
        out = self.runs
        if kw.get("workflow_id") is not None:
            out = [r for r in out if r["workflow_id"] == kw["workflow_id"]]
        if kw.get("branch") is not None:
            out = [r for r in out if r["head_branch"] == kw["branch"]]
        if kw.get("status") is not None:
            out = [r for r in out if r["conclusion"] == kw["status"]]
        return sorted(out, key=lambda r: r["created_at"], reverse=True)[: kw.get("per_page", 30)]

    def get_jobs(self, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        self.calls.append(("get_jobs", {"run_id": run_id}))
        return self.jobs

    def compare(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
        self.calls.append(("compare", {"base": base, "head": head}))
        return {"commits": [], "files": []}

    def download_logs(self, owner: str, repo: str, job_id: int) -> str:
        self.calls.append(("download_logs", {"job_id": job_id}))
        return ""


def _run(run_id: int, wf: int, created: str, conclusion: str, branch: str = "main") -> dict[str, Any]:
    return {
        "id": run_id,
        "workflow_id": wf,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "head_branch": branch,
        "head_sha": f"{run_id:040d}",
        "created_at": created,
        "conclusion": conclusion,
    }


def test_live_mode_picks_newest_success_before_the_failure_on_same_branch() -> None:
    runs = [
        _run(10, 7, "2026-10-23T12:00:00Z", "failure"),
        _run(9, 7, "2026-10-23T11:00:00Z", "success"),
        _run(8, 7, "2026-10-23T10:00:00Z", "success"),
        _run(7, 7, "2026-10-23T13:30:00Z", "success"),  # after the failure: not eligible
        _run(6, 7, "2026-10-23T11:30:00Z", "success", branch="release"),  # wrong branch
        _run(5, 99, "2026-10-23T11:45:00Z", "success"),  # wrong workflow
    ]
    client = FakeGitHub(runs)
    target = parse_target("acme/widgets")
    got = github_runs.collect(CollectContext(settings=settings(), target=target, sample=None), client)
    assert got.failed_run.run_id == 10
    assert got.last_green_run is not None
    assert got.last_green_run.run_id == 9


def test_live_mode_with_no_green_run_returns_none_and_no_green_evidence() -> None:
    runs = [_run(10, 7, "2026-10-23T12:00:00Z", "failure")]
    client = FakeGitHub(runs)
    target = parse_target("acme/widgets")
    got = github_runs.collect(CollectContext(settings=settings(), target=target, sample=None), client)
    assert got.last_green_run is None
    assert [e.id for e in got.evidence] == ["ev_run_10"]


def test_live_mode_requires_a_client() -> None:
    target = parse_target("acme/widgets")
    with pytest.raises(CollectorError):
        github_runs.collect(CollectContext(settings=settings(), target=target, sample=None), None)


# ---------------------------------------------------------------- diff


def test_diff_collector_returns_commits_and_files() -> None:
    runs = github_runs.collect(ctx("dependency_bump"))
    got = diff_collector.collect(ctx("dependency_bump"), runs)
    assert len(got.commits) == 2
    assert got.commits[0].author == "dependabot[bot]"
    assert "fastapi" in got.commits[0].message
    assert [c.filename for c in got.files] == ["pyproject.toml", "README.md"]


def test_diff_evidence_ids_are_stable() -> None:
    runs = github_runs.collect(ctx("dependency_bump"))
    got = diff_collector.collect(ctx("dependency_bump"), runs)
    ids = [e.id for e in got.evidence]
    assert ids[0] == "ev_commit_3f9c2a"
    assert "ev_file_001" in ids and "ev_file_002" in ids
    assert [e.kind for e in got.evidence] == ["commit", "commit", "file", "file"]


def test_diff_compares_last_green_sha_to_failed_sha() -> None:
    runs = github_runs.collect(ctx("bad_commit"))
    client = FakeGitHub([])
    target = parse_target("acme/widgets")
    live = CollectContext(settings=settings(), target=target, sample=None)
    diff_collector.collect(live, runs, client)
    assert runs.last_green_run is not None
    assert (
        "compare",
        {"base": runs.last_green_run.head_sha, "head": runs.failed_run.head_sha},
    ) in client.calls


def test_diff_patch_is_capped_at_200_lines(tmp_path: Path) -> None:
    case = tmp_path / "longpatch"
    case.mkdir()
    (case / "diff.json").write_text(
        json.dumps(
            {
                "commits": [],
                "files": [
                    {
                        "filename": "big.py",
                        "status": "modified",
                        "additions": 500,
                        "deletions": 0,
                        "patch": "\n".join(f"+line {i}" for i in range(500)),
                    }
                ],
            }
        )
    )
    c = CollectContext(settings=settings(tmp_path), sample="longpatch")
    runs = github_runs.collect(ctx("bad_commit"))
    got = diff_collector.collect(c, runs)
    assert got.evidence[0].metadata["patch_lines"] == 200
    assert len(got.evidence[0].excerpt.splitlines()) == 200


def test_diff_without_a_green_run_produces_no_evidence() -> None:
    runs = github_runs.RunsCollected(
        failed_run=github_runs.collect(ctx("bad_commit")).failed_run,
        last_green_run=None,
        evidence=[],
    )
    got = diff_collector.collect(ctx("bad_commit"), runs)
    assert got.evidence == []
    assert got.commits == []


# ---------------------------------------------------------------- logs


def test_logs_extracts_dependency_resolution_error() -> None:
    runs = github_runs.collect(ctx("dependency_bump"))
    got = logs.collect(ctx("dependency_bump"), runs)
    patterns = {m.pattern for m in got.matches}
    assert "dependency_resolution" in patterns
    assert any("no solution" in e.excerpt for e in got.evidence if not e.metadata.get("tail"))


def test_logs_ids_are_sequential() -> None:
    runs = github_runs.collect(ctx("bad_commit"))
    got = logs.collect(ctx("bad_commit"), runs)
    window_ids = [e.id for e in got.evidence if not e.metadata.get("tail")]
    assert window_ids == [f"ev_log_{i:03d}" for i in range(1, len(window_ids) + 1)]


def test_logs_keeps_last_300_lines_as_tail(tmp_path: Path) -> None:
    case = tmp_path / "longlog"
    case.mkdir()
    body = "\n".join(f"step line {i}" for i in range(500))
    (case / "run.json").write_text(json.dumps(json.loads((SAMPLES / "bad_commit" / "run.json").read_text())))
    (case / "logs.json").write_text(json.dumps({"jobs": [{"job_id": 90021, "name": "test", "log": body}]}))
    c = CollectContext(settings=settings(tmp_path), sample="longlog")
    runs = github_runs.collect(c)
    got = logs.collect(c, runs)
    tails = [e for e in got.evidence if e.metadata.get("tail")]
    assert len(tails) == 1
    assert tails[0].metadata["lines"] == 300
    assert len(tails[0].excerpt.splitlines()) == 300
    assert tails[0].excerpt.splitlines()[-1] == "step line 499"


def test_overlapping_matches_merge_into_one_window(tmp_path: Path) -> None:
    case = tmp_path / "overlap"
    case.mkdir()
    log = "\n".join(
        ["Traceback (most recent call last):"]
        + [f'  File "/app/x.py", line {i}, in f' for i in range(10)]
        + ["ValueError: bad value", "##[error]timeout after 30s"]
    )
    (case / "run.json").write_text((SAMPLES / "bad_commit" / "run.json").read_text())
    (case / "logs.json").write_text(json.dumps({"jobs": [{"job_id": 90021, "name": "test", "log": log}]}))
    c = CollectContext(settings=settings(tmp_path), sample="overlap")
    got = logs.collect(c, github_runs.collect(c))
    assert len([e for e in got.evidence if not e.metadata.get("tail")]) == 1


def test_logs_excerpts_are_redacted_before_leaving_the_collector(tmp_path: Path) -> None:
    case = tmp_path / "secretlog"
    case.mkdir()
    fake = "ghp_FAKEfakeFAKEfake1234FAKEfake1234"
    log = f"Using GITHUB_TOKEN={fake}\nError: 401 Unauthorized"
    (case / "run.json").write_text((SAMPLES / "bad_commit" / "run.json").read_text())
    (case / "logs.json").write_text(json.dumps({"jobs": [{"job_id": 90021, "name": "test", "log": log}]}))
    c = CollectContext(settings=settings(tmp_path), sample="secretlog")
    got = logs.collect(c, github_runs.collect(c))
    joined = "\n".join(e.excerpt for e in got.evidence)
    assert fake not in joined
    assert REDACTED in joined


def test_logs_with_no_error_lines_still_returns_a_tail() -> None:
    runs = github_runs.collect(ctx("dependency_bump"))
    got = logs.collect(ctx("dependency_bump"), runs)
    assert [e for e in got.evidence if e.metadata.get("tail")]


def test_missing_fixture_raises_collector_error() -> None:
    with pytest.raises(CollectorError):
        github_runs.collect(ctx("does_not_exist"))


@pytest.mark.parametrize("sample", ["dependency_bump", "bad_commit", "expired_or_missing_secret"])
def test_every_evidence_has_a_source_url_and_timestamp(sample: str) -> None:
    runs = github_runs.collect(ctx(sample))
    d = diff_collector.collect(ctx(sample), runs)
    lg = logs.collect(ctx(sample), runs)
    for ev in runs.evidence + d.evidence + lg.evidence:
        assert ev.source_url.startswith("https://github.com/acme/widgets")
        assert ev.id
        assert ev.title


def test_coloured_compiler_errors_are_found_and_shown_without_escapes() -> None:
    lines = [
        "2026-10-14T23:22:54.1Z ##[group]Run # https://example.com/t-cargo/topic/timeout.20investigation",
        "2026-10-14T23:22:54.2Z \x1b[36;1mcargo test --locked\x1b[0m",
        "2026-10-14T23:23:05.1Z \x1b[1m\x1b[91merror[E0432]\x1b[0m\x1b[1m: unresolved import `ratatui`\x1b[0m",
        "2026-10-14T23:23:05.2Z \x1b[1m\x1b[91merror\x1b[0m: could not compile `sweepr` due to 10 previous errors",
        "2026-10-14T23:23:05.3Z ##[error]Process completed with exit code 101.",
    ]
    hits = logs._hits(lines)
    assert [name for _, name in hits] == ["compile_error", "compile_error"]


def test_the_tail_ends_at_the_failing_step_not_the_post_job_cleanup() -> None:
    lines = [f"line {i}" for i in range(10)] + ["##[error]Process completed with exit code 1."]
    lines += ["Post job cleanup.", "git config --unset-all http.extraheader", "Cleaning up orphan processes"]
    tail = logs._tail(lines)
    assert tail[-1].startswith("##[error]")
    assert "Cleaning up orphan processes" not in tail


def test_pytest_prefixed_import_errors_are_python_errors() -> None:
    hits = logs._hits(["E   ModuleNotFoundError: No module named 'ugbo'"])
    assert hits == [(0, "python_error")]


class _FirstRunGitHub(FakeGitHub):
    def compare(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
        self.calls.append(("compare", {"base": base, "head": head}))
        return {
            "commits": [{"sha": head, "commit": {"message": "add the test workflow"}}],
            "files": [{"filename": ".github/workflows/test.yml", "status": "added", "patch": "+run: pytest -q"}],
        }


def test_a_first_run_with_no_green_baseline_diffs_the_commit_against_its_parent() -> None:
    runs = [_run(10, 7, "2026-10-23" + "T12:00:00Z", "failure")]
    client = _FirstRunGitHub(runs)
    target = parse_target("acme/widgets")
    context = CollectContext(settings=settings(), target=target, sample=None)
    got_runs = github_runs.collect(context, client)
    assert got_runs.last_green_run is None
    changes = diff_collector.collect(context, got_runs, client)
    head = got_runs.failed_run.head_sha
    assert ("compare", {"base": f"{head}~1", "head": head}) in client.calls
    assert [item.filename for item in changes.files] == [".github/workflows/test.yml"]
