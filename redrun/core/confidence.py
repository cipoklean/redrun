"""Computed confidence. The model never supplies this number; these signals do.

Weights live here and nowhere else. They are a small, explainable rubric, not a
calibrated probability: read `confidence` as "how much of the rubric fired".
"""

from redrun.core.models import ConfidenceSignal, Link, Verdict
from redrun.core.taxonomy import Cause

WEIGHTS: dict[str, float] = {
    # The cross-source query's own ranking, taken at face value.
    "link_score": 0.25,
    # Three or more independent supporting ids is treated as fully supported.
    "supporting_ids": 0.20,
    # Share of evaluated skeptic checks that passed.
    "skeptic_pass_ratio": 0.25,
    # Share of the decisive checks that passed.
    "decisive_passes": 0.15,
    # Every revision means the first answer was wrong, so it costs.
    "revision_penalty": -0.10,
    # Any evidence pointing the other way is a real cost, not a rounding error.
    "against_penalty": -0.15,
}

UNKNOWN_CAP = 0.5
DECISIVE_CHECKS = (
    "same_sha_elsewhere",
    "test_failure_history",
    "error_change_overlap",
    "outage_overlap",
    "dependency_timing",
    "citation_validity",
)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_confidence(
    *,
    links: list[Link],
    cited_ids: list[str],
    checks: list[dict],
    revisions: int,
    against_ids: list[str],
    cause: Cause,
) -> tuple[float, list[ConfidenceSignal]]:
    """A weighted sum of six signals, clamped, with a hard cap for `unknown`."""
    evaluated = [c for c in checks if c.get("status") in ("pass", "fail")]
    passed = [c for c in evaluated if c.get("status") == "pass"]
    decisive = [c for c in evaluated if c.get("check") in DECISIVE_CHECKS]

    cited_set = set(cited_ids)
    linked = [
        link.score_hint
        for link in links
        if link.log_evidence_id in cited_set or link.change_evidence_id in cited_set
    ]
    link_score = max(linked) if linked else max((l.score_hint for l in links), default=0.0)

    signals = [
        ConfidenceSignal(signal="link_score", weight=WEIGHTS["link_score"], value=_clamp(link_score)),
        ConfidenceSignal(
            signal="supporting_ids",
            weight=WEIGHTS["supporting_ids"],
            value=_clamp(len(cited_set) / 3.0),
        ),
        ConfidenceSignal(
            signal="skeptic_pass_ratio",
            weight=WEIGHTS["skeptic_pass_ratio"],
            value=(len(passed) / len(evaluated)) if evaluated else 0.0,
        ),
        ConfidenceSignal(
            signal="decisive_passes",
            weight=WEIGHTS["decisive_passes"],
            value=(len([c for c in decisive if c.get("status") == "pass"]) / len(DECISIVE_CHECKS)),
        ),
        ConfidenceSignal(
            signal="revision_penalty",
            weight=WEIGHTS["revision_penalty"],
            value=_clamp(float(revisions)),
        ),
        ConfidenceSignal(
            signal="against_penalty",
            weight=WEIGHTS["against_penalty"],
            value=1.0 if against_ids else 0.0,
        ),
    ]

    raw = sum(signal.weight * signal.value for signal in signals)
    confidence = _clamp(raw)
    if cause is Cause.UNKNOWN:
        confidence = min(confidence, UNKNOWN_CAP)
    return round(confidence, 4), signals


def apply_confidence(verdict: Verdict, checks: list[dict], revisions: int, links: list[Link]) -> Verdict:
    """Return the same verdict with confidence and breakdown replaced by computed ones."""
    checks_payload = [check.model_dump() if hasattr(check, "model_dump") else check for check in checks]
    confidence, signals = compute_confidence(
        links=links,
        cited_ids=verdict.cited_evidence_ids,
        checks=checks_payload,
        revisions=revisions,
        against_ids=[],
        cause=verdict.cause,
    )
    return verdict.model_copy(update={"confidence": confidence, "confidence_breakdown": signals})
