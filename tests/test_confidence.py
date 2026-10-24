"""Computed confidence: weights, clamping, the unknown cap, and monotonicity."""

import pytest

from redrun.core.confidence import DECISIVE_CHECKS, UNKNOWN_CAP, WEIGHTS, compute_confidence
from redrun.core.models import Link
from redrun.core.taxonomy import Cause

LINKS = [
    Link(
        link_type="dependency_match",
        log_evidence_id="ev_log_001",
        change_evidence_id="ev_dep_fastapi",
        score_hint=0.85,
        detail="x",
    )
]


def checks(pass_count: int, fail_count: int, decisive_pass: int = 2) -> list[dict]:
    collected = [
        {"check": "same_sha_elsewhere", "status": "pass" if decisive_pass > 0 else "fail"}
    ]
    for index in range(pass_count):
        collected.append({"check": f"extra_pass_{index}", "status": "pass"})
    for index in range(fail_count):
        collected.append({"check": f"extra_fail_{index}", "status": "fail"})
    return collected


def score(**overrides):
    payload = dict(
        links=LINKS,
        cited_ids=["ev_log_001", "ev_dep_fastapi"],
        checks=checks(2, 0),
        revisions=0,
        against_ids=[],
        cause=Cause.DEPENDENCY_BUMP,
    )
    payload.update(overrides)
    return compute_confidence(**payload)


def test_weights_are_documented_in_one_place() -> None:
    assert set(WEIGHTS) == {
        "link_score",
        "supporting_ids",
        "skeptic_pass_ratio",
        "decisive_passes",
        "revision_penalty",
        "against_penalty",
    }
    assert len(DECISIVE_CHECKS) == 5


def test_confidence_is_the_sum_of_the_breakdown() -> None:
    confidence, signals = score()
    assert confidence == pytest.approx(
        sum(signal.weight * signal.value for signal in signals), abs=1e-4
    )


def test_confidence_is_clamped() -> None:
    confidence, _ = score(checks=checks(20, 0), cited_ids=["a", "b", "c", "d"])
    assert 0.0 <= confidence <= 1.0


def test_more_citations_never_lower_confidence() -> None:
    low, _ = score(cited_ids=["ev_log_001"])
    high, _ = score(cited_ids=["ev_log_001", "ev_dep_fastapi", "ev_file_001"])
    assert high >= low


def test_a_failed_skeptic_check_never_raises_confidence() -> None:
    clean, _ = score(checks=checks(3, 0))
    dirty, _ = score(checks=checks(2, 1))
    assert dirty <= clean


def test_a_failed_decisive_check_never_raises_confidence() -> None:
    clean, _ = score(checks=checks(1, 0, decisive_pass=1))
    dirty, _ = score(checks=checks(1, 0, decisive_pass=0))
    assert dirty <= clean


def test_a_revision_costs_confidence() -> None:
    once, _ = score(revisions=0)
    twice, _ = score(revisions=1)
    assert twice < once


def test_against_ids_cost_confidence() -> None:
    clean, _ = score(against_ids=[])
    contradicted, _ = score(against_ids=["ev_file_001"])
    assert contradicted < clean


def test_unknown_is_capped() -> None:
    confidence, _ = score(cause=Cause.UNKNOWN, checks=checks(20, 0))
    assert confidence <= UNKNOWN_CAP


def test_breakdown_names_all_six_signals() -> None:
    _, signals = score()
    assert {signal.signal for signal in signals} == set(WEIGHTS)
