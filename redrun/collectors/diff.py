"""What changed between the last green commit and the failing one."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from redrun.collectors.base import CollectContext, CollectorError, GitHubAPI
from redrun.collectors.github_runs import RunsCollected, parse_time
from redrun.core.models import Evidence
from redrun.core.redact import redact

PATCH_LINE_CAP = 200


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    date: datetime | None = None


@dataclass
class FileChange:
    filename: str
    status: str
    additions: int
    deletions: int
    patch_lines: int


@dataclass
class DiffCollected:
    commits: list[CommitInfo] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


def _commit(raw: dict[str, Any]) -> CommitInfo:
    """Accept both the flat fixture shape and GitHub's nested compare shape."""
    inner = raw.get("commit") if isinstance(raw.get("commit"), dict) else {}
    author_block = inner.get("author") if isinstance(inner.get("author"), dict) else {}
    date_raw = author_block.get("date") or raw.get("date")
    return CommitInfo(
        sha=str(raw.get("sha", "")),
        message=str(inner.get("message") or raw.get("message") or ""),
        author=str(author_block.get("name") or raw.get("author") or "unknown"),
        date=parse_time(date_raw) if date_raw else None,
    )


def collect(
    ctx: CollectContext, runs: RunsCollected, client: GitHubAPI | None = None
) -> DiffCollected:
    if runs.last_green_run is None:
        # No green baseline means there is no range to compare, and guessing one
        # would attribute unrelated history to this failure.
        return DiffCollected()

    if ctx.is_sample:
        data = ctx.fixture("diff.json")
    else:
        if client is None:
            raise CollectorError("a GitHub client is required outside sample mode")
        data = client.compare(
            runs.owner, runs.repo, runs.last_green_run.head_sha, runs.failed_run.head_sha
        )

    commits = [_commit(raw) for raw in data.get("commits", [])]
    out = DiffCollected(commits=commits)

    for commit in commits:
        out.evidence.append(
            Evidence(
                id=f"ev_commit_{commit.sha[:6]}",
                kind="commit",
                source_url=f"https://github.com/{runs.owner}/{runs.repo}/commit/{commit.sha}",
                title=f"Commit {commit.sha[:7]}: {commit.message.splitlines()[0][:80]}",
                excerpt=redact(commit.message),
                timestamp=commit.date,
                metadata={"sha": commit.sha, "author": commit.author, "message": commit.message},
            )
        )

    for index, raw in enumerate(data.get("files", []), start=1):
        filename = str(raw.get("filename", ""))
        patch_lines = (raw.get("patch") or "").splitlines()[:PATCH_LINE_CAP]
        change = FileChange(
            filename=filename,
            status=str(raw.get("status", "modified")),
            additions=int(raw.get("additions", 0)),
            deletions=int(raw.get("deletions", 0)),
            patch_lines=len(patch_lines),
        )
        out.files.append(change)
        out.evidence.append(
            Evidence(
                id=f"ev_file_{index:03d}",
                kind="file",
                source_url=(
                    f"https://github.com/{runs.owner}/{runs.repo}/blob/"
                    f"{runs.failed_run.head_sha}/{filename}"
                ),
                title=f"{change.status}: {filename} (+{change.additions}/-{change.deletions})",
                excerpt=redact("\n".join(patch_lines)),
                timestamp=runs.failed_run.created_at,
                metadata={
                    "filename": filename,
                    "status": change.status,
                    "additions": change.additions,
                    "deletions": change.deletions,
                    "patch_lines": change.patch_lines,
                },
            )
        )

    return out
