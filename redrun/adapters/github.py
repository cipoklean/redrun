"""Thin GitHub REST client. Only the calls the collectors need."""

from typing import Any

import httpx

from redrun.collectors.base import CollectorError

API_ROOT = "https://api.github.com"


class GitHubClient:
    def __init__(
        self,
        token: str = "",
        *,
        base_url: str = API_ROOT,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "redrun/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        response = self._client.get(path, params=clean)
        if response.status_code >= 400:
            raise CollectorError(
                f"GitHub {response.status_code} on {path}: {response.text[:200]}"
            )
        return response.json()

    def get_default_branch(self, owner: str, repo: str) -> str:
        data = self._get(f"/repos/{owner}/{repo}")
        return str(data.get("default_branch") or "main")

    def get_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}/actions/runs/{run_id}")

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
    ) -> list[dict[str, Any]]:
        data = self._get(
            f"/repos/{owner}/{repo}/actions/runs",
            workflow_id=workflow_id,
            branch=branch,
            status=status,
            head_sha=head_sha,
            per_page=per_page,
        )
        return list(data.get("workflow_runs") or [])

    def get_jobs(self, owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
        data = self._get(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", per_page=100)
        return list(data.get("jobs") or [])

    def compare(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}/compare/{base}...{head}")

    def download_logs(self, owner: str, repo: str, job_id: int) -> str:
        response = self._client.get(f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
        if response.status_code >= 400:
            raise CollectorError(
                f"GitHub {response.status_code} downloading job {job_id} logs: "
                f"{response.text[:200]}"
            )
        return response.text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
