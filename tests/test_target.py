"""Input parsing accepts a run URL or an owner/repo slug and nothing else."""

import pytest

from redrun.core.target import Target, parse_target


def test_run_url_parses_to_run_kind() -> None:
    t = parse_target("https://github.com/acme/widgets/actions/runs/12345")
    assert t == Target(kind="run", owner="acme", repo="widgets", run_id=12345)


def test_run_url_tolerates_scheme_slash_and_query() -> None:
    t = parse_target("http://github.com/acme/widgets/actions/runs/999/?check_suite_focus=true")
    assert t.kind == "run"
    assert t.run_id == 999


def test_slug_parses_to_repo_kind() -> None:
    t = parse_target("acme/widgets")
    assert t == Target(kind="repo", owner="acme", repo="widgets", run_id=None)


def test_repo_url_without_run_parses_to_repo_kind() -> None:
    t = parse_target("https://github.com/acme/widgets")
    assert t.kind == "repo"
    assert (t.owner, t.repo) == ("acme", "widgets")


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "acme", "acme/", "/widgets", "acme/widgets/actions", "https://gitlab.com/a/b", "a/b/c"],
)
def test_invalid_targets_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_target(bad)


def test_run_url_without_numeric_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_target("https://github.com/acme/widgets/actions/runs/notanumber")
