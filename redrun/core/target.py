"""Input parsing: a run URL or an owner/repo slug. Nothing else is accepted."""

import re
from dataclasses import dataclass
from typing import Literal

_RUN_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/actions/runs/(\d+)/?(?:[?#].*)?$"
)
_REPO_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/?$"
)
_SLUG = re.compile(r"^([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)$")


@dataclass(frozen=True)
class Target:
    kind: Literal["run", "repo"]
    owner: str
    repo: str
    run_id: int | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_target(raw: str) -> Target:
    """Parse a GitHub run URL or an owner/repo slug.

    Raises ValueError for anything else, so a typo fails at the edge instead of
    turning into a confusing 404 from the GitHub API.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty target")

    m = _RUN_URL.match(text)
    if m:
        return Target(kind="run", owner=m.group(1), repo=m.group(2), run_id=int(m.group(3)))

    m = _REPO_URL.match(text)
    if m:
        return Target(kind="repo", owner=m.group(1), repo=m.group(2), run_id=None)

    if "/actions/" in text:
        raise ValueError(f"not a GitHub Actions run URL: {raw!r}")

    m = _SLUG.match(text)
    if m:
        return Target(kind="repo", owner=m.group(1), repo=m.group(2), run_id=None)

    raise ValueError(f"expected a GitHub run URL or owner/repo, got {raw!r}")
