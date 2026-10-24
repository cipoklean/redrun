"""Find the failed run and the last green run of the same workflow on the same branch."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from redrun.collectors.base import CollectContext, CollectorError, GitHubAPI
from redrun.core.models import Evidence, JobInfo, RunInfo, StepInfo
from redrun.core.redact import redact


@dataclass
class RunsCollected:
    failed_run: RunInfo
    last_green_run: RunInfo | None
    evidence: list[Evidence]
    owner: str = ""
    repo: str = ""


def parse_time(value: str) -> datetime:
    """GitHub returns `2026-10-23T09:14:02Z`; fromisoformat wants +00:00."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _jobs(raw_jobs: list[dict[str, Any]]) -> list[JobInfo]:
    return [
        JobInfo(
            job_id=int(j.get("job_id") or j.get("id")),
            name=j.get("name", ""),
            conclusion=j.get("conclusion") or "unknown",
            steps=[
                StepInfo(
                    number=int(s.get("number", i + 1)),
                    name=s.get("name", ""),
                    conclusion=s.get("conclusion") or "unknown",
                )
                for i, s in enumerate(j.get("steps", []))
            ],
        )
        for j in raw_jobs
    ]


def _runner_image(raw_jobs: list[dict[str, Any]]) -> str | None:
    for job in raw_jobs:
        labels = job.get("labels") or []
        if labels:
            return str(labels[0])
        if job.get("runner_name"):
            return str(job["runner_name"])
    return None


def _failing_steps(run: RunInfo) -> list[str]:
    """Steps that failed. `skipped` is not a failure: nothing ran."""
    out: list[str] = []
    for job in run.jobs:
        for step in job.steps:
            if step.conclusion not in ("success", "skipped", "neutral"):
                out.append(step.name)
    return out


def _run_evidence(run: RunInfo, owner: str, repo: str, label: str) -> Evidence:
    failing = _failing_steps(run)
    excerpt = (
        f"{label}: workflow {run.workflow_name} on {run.branch} at {run.head_sha[:7]} "
        f"concluded {run.conclusion}; runner {run.runner_image or 'unknown'}; "
        f"failing steps: {', '.join(failing) or 'none'}"
    )
    return Evidence(
        id=f"ev_run_{run.run_id}",
        kind="run",
        source_url=f"https://github.com/{owner}/{repo}/actions/runs/{run.run_id}",
        title=f"{label}: {run.workflow_name} #{run.run_id}",
        excerpt=redact(excerpt),
        timestamp=run.created_at,
        metadata={
            "run_id": run.run_id,
            "head_sha": run.head_sha,
            "conclusion": run.conclusion,
            "workflow": run.workflow_name,
            "branch": run.branch,
            "runner_image": run.runner_image,
            "failing_steps": failing,
        },
    )


def _repo_from(raw: dict[str, Any], ctx: CollectContext) -> tuple[str, str]:
    repo = raw.get("repository") or {}
    owner = repo.get("owner")
    name = repo.get("repo")
    if owner and name:
        return str(owner), str(name)
    target = ctx.target
    if target is None:
        raise CollectorError("fixture has no repository and no target was supplied")
    return target.owner, target.repo


def _from_fixture(ctx: CollectContext) -> RunsCollected:
    data = ctx.fixture("run.json")
    owner, repo = _repo_from(data, ctx)
    workflow = data.get("workflow") or {}
    branch = data.get("branch") or "main"
    runner = data.get("runner_image")

    def build(raw: dict[str, Any]) -> RunInfo:
        jobs = _jobs(raw.get("jobs") or [])
        return RunInfo(
            run_id=int(raw["run_id"]),
            workflow_name=workflow.get("name", ""),
            workflow_path=workflow.get("path", ""),
            branch=branch,
            head_sha=raw["head_sha"],
            created_at=parse_time(raw["created_at"]),
            conclusion=raw["conclusion"],
            runner_image=runner,
            jobs=jobs,
        )

    failed = build(data["failed_run"])
    green = build(data["last_green_run"]) if data.get("last_green_run") else None
    evidence = [_run_evidence(failed, owner, repo, "Failed run")]
    if green is not None:
        evidence.append(_run_evidence(green, owner, repo, "Last green run"))
    return RunsCollected(failed, green, evidence, owner=owner, repo=repo)


def _run_from_api(raw: dict[str, Any], jobs: list[dict[str, Any]]) -> RunInfo:
    return RunInfo(
        run_id=int(raw["id"]),
        workflow_name=raw.get("name") or "",
        workflow_path=raw.get("path") or "",
        branch=raw.get("head_branch") or "",
        head_sha=raw.get("head_sha") or "",
        created_at=parse_time(raw["created_at"]),
        conclusion=raw.get("conclusion") or "unknown",
        runner_image=_runner_image(jobs),
        jobs=_jobs(jobs),
    )


def collect(ctx: CollectContext, client: GitHubAPI | None = None) -> RunsCollected:
    """Sample mode reads run.json; live mode resolves the run and its last green sibling."""
    if ctx.is_sample:
        return _from_fixture(ctx)

    target = ctx.require_target()
    if client is None:
        raise CollectorError("a GitHub client is required outside sample mode")
    owner, repo = target.owner, target.repo

    if target.kind == "run" and target.run_id is not None:
        raw = client.get_run(owner, repo, target.run_id)
    else:
        branch = client.get_default_branch(owner, repo)
        runs = client.list_workflow_runs(owner, repo, branch=branch, status="failure", per_page=30)
        if not runs:
            raise CollectorError(f"no failed runs on the default branch of {owner}/{repo}")
        raw = max(runs, key=lambda r: parse_time(r["created_at"]))

    branch = raw.get("head_branch") or ""
    workflow_id = raw.get("workflow_id")
    failed = _run_from_api(raw, client.get_jobs(owner, repo, int(raw["id"])))

    green: RunInfo | None = None
    candidates = client.list_workflow_runs(
        owner, repo, workflow_id=workflow_id, branch=branch, status="success", per_page=30
    )
    earlier = [r for r in candidates if parse_time(r["created_at"]) < failed.created_at]
    if earlier:
        chosen = max(earlier, key=lambda r: parse_time(r["created_at"]))
        green = _run_from_api(chosen, client.get_jobs(owner, repo, int(chosen["id"])))

    evidence = [_run_evidence(failed, owner, repo, "Failed run")]
    if green is not None:
        evidence.append(_run_evidence(green, owner, repo, "Last green run"))
    return RunsCollected(failed, green, evidence, owner=owner, repo=repo)
