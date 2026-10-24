"""Shared collector plumbing: the context, the error type, and the GitHub surface."""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from redrun.config import Settings
from redrun.core.target import Target


class CollectorError(RuntimeError):
    """A collector could not produce evidence. Callers turn this into an error event."""


class GitHubAPI(Protocol):
    """The slice of GitHub REST that collectors use. Keeps collectors testable."""

    def get_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]: ...

    def list_workflow_runs(
        self,
        owner: str,
        repo: str,
        *,
        workflow_id: int | str | None = None,
        branch: str | None = None,
        status: str | None = None,
        head_sha: str | None = None,
        per_page: int = 30,
    ) -> list[dict[str, Any]]: ...

    def get_jobs(self, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]: ...

    def compare(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]: ...

    def download_logs(self, owner: str, repo: str, job_id: int) -> str: ...


@dataclass
class CollectContext:
    """Everything a collector needs, and the one way to read a fixture."""

    settings: Settings
    target: Target | None = None
    sample: str | None = None

    @property
    def is_sample(self) -> bool:
        return self.settings.sample_mode and bool(self.sample)

    def fixture(self, name: str) -> Any:
        if not self.sample:
            raise CollectorError("no sample selected")
        path = self.settings.samples_dir / self.sample / name
        if not path.exists():
            raise CollectorError(f"missing fixture: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def require_target(self) -> Target:
        if self.target is None:
            raise CollectorError("a GitHub target is required outside sample mode")
        return self.target
