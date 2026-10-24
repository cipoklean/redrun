"""Citation guard: a verdict may only cite evidence that exists in this run."""

import re
from dataclasses import dataclass, field

from redrun.core.models import Verdict
from redrun.core.taxonomy import Cause

EV_TOKEN = re.compile(r"\bev_[a-z0-9_]+\b")


@dataclass
class CitationProblem:
    code: str
    detail: str


@dataclass
class CitationReport:
    ok: bool
    problems: list[CitationProblem] = field(default_factory=list)

    def reason(self) -> str:
        return "; ".join(f"{p.code}: {p.detail}" for p in self.problems)


def guard(verdict: Verdict, evidence_ids: set[str]) -> CitationReport:
    """Three rules: every cited id exists, a real cause cites something, and any
    ev_ token inside root_cause also exists. An unknown cause may cite nothing."""
    problems: list[CitationProblem] = []

    missing = [cited for cited in verdict.cited_evidence_ids if cited not in evidence_ids]
    for cited in missing:
        problems.append(CitationProblem("missing_citation", f"{cited} is not in this run's evidence"))

    if verdict.cause is not Cause.UNKNOWN and not verdict.cited_evidence_ids:
        problems.append(
            CitationProblem("no_citation", f"cause {verdict.cause.value} cites no evidence")
        )

    for token in EV_TOKEN.findall(verdict.root_cause):
        if token not in evidence_ids:
            problems.append(
                CitationProblem("missing_text_citation", f"root_cause mentions {token}, which does not exist")
            )

    seen: set[str] = set()
    unique: list[CitationProblem] = []
    for problem in problems:
        key = f"{problem.code}:{problem.detail}"
        if key not in seen:
            seen.add(key)
            unique.append(problem)
    return CitationReport(ok=not unique, problems=unique)
