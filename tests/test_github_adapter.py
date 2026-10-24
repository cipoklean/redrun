"""The GitHub client: correct paths, token handling, and no token leakage on redirect."""

import httpx
import pytest

from redrun.adapters.github import GitHubClient
from redrun.collectors.base import CollectorError


def test_get_run_hits_the_actions_path_and_sends_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 5001})

    client = GitHubClient("gh-token", transport=httpx.MockTransport(handler))
    assert client.get_run("acme", "widgets", 5001)["id"] == 5001
    assert seen[0].url.path == "/repos/acme/widgets/actions/runs/5001"
    assert seen[0].headers["authorization"] == "Bearer gh-token"


def test_list_workflow_runs_unwraps_and_filters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"workflow_runs": [{"id": 1}, {"id": 2}]})

    client = GitHubClient("t", transport=httpx.MockTransport(handler))
    runs = client.list_workflow_runs("acme", "widgets", workflow_id=7, branch="main", status="success")
    assert len(runs) == 2
    assert seen[0].url.params["branch"] == "main"
    assert seen[0].url.params["status"] == "success"
    assert seen[0].url.params["workflow_id"] == "7"


def test_http_error_becomes_collector_error_with_status() -> None:
    client = GitHubClient(
        "t",
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "Not Found"})),
    )
    with pytest.raises(CollectorError) as excinfo:
        client.get_run("acme", "widgets", 1)
    assert "404" in str(excinfo.value)


def test_download_logs_follows_the_redirect_without_leaking_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(
                302, headers={"Location": "https://objects.test/run.log"}
            )
        return httpx.Response(200, text="line one\nTraceback (most recent call last):")

    client = GitHubClient("gh-token", transport=httpx.MockTransport(handler))
    body = client.download_logs("acme", "widgets", 90021)

    assert "Traceback" in body
    assert len(seen) == 2
    assert seen[1].url.host == "objects.test"
    assert "authorization" not in {k.lower() for k in seen[1].headers}


def test_no_token_means_no_authorization_header() -> None:
    seen: list[httpx.Request] = []
    client = GitHubClient(
        "", transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200, json={}))[1])
    )
    client.get_default_branch("acme", "widgets")
    assert "authorization" not in {k.lower() for k in seen[0].headers}


def test_compare_path_uses_triple_dot() -> None:
    seen: list[httpx.Request] = []
    client = GitHubClient(
        "t",
        transport=httpx.MockTransport(
            lambda r: (seen.append(r), httpx.Response(200, json={"commits": [], "files": []}))[1]
        ),
    )
    client.compare("acme", "widgets", "abc123", "def456")
    assert seen[0].url.path == "/repos/acme/widgets/compare/abc123...def456"


def test_client_is_a_context_manager() -> None:
    with GitHubClient("t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as c:
        assert c.get_default_branch("acme", "widgets") == "main"
