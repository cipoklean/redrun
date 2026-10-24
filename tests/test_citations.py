"""The citation guard: three rules, each with its own failing case."""

import pytest

from redrun.core.citations import guard
from redrun.core.models import Verdict
from redrun.core.taxonomy import Cause

EVIDENCE = {"ev_log_001", "ev_file_001"}


def verdict(**overrides) -> Verdict:
    payload = dict(
        cause=Cause.BAD_COMMIT,
        root_cause="the refactor broke models.py (see ev_file_001)",
        confidence=0.5,
        confidence_breakdown=[],
        next_step="fix it",
        cited_evidence_ids=["ev_log_001"],
    )
    payload.update(overrides)
    return Verdict(**payload)


def test_valid_verdict_passes() -> None:
    assert guard(verdict(), EVIDENCE).ok is True


def test_citing_an_unknown_id_fails() -> None:
    report = guard(verdict(cited_evidence_ids=["ev_log_999"]), EVIDENCE)
    assert report.ok is False
    assert any(p.code == "missing_citation" for p in report.problems)


def test_real_cause_with_no_citations_fails() -> None:
    report = guard(verdict(cited_evidence_ids=[]), EVIDENCE)
    assert report.ok is False
    assert any(p.code == "no_citation" for p in report.problems)


def test_unknown_cause_may_cite_nothing() -> None:
    report = guard(
        verdict(cause=Cause.UNKNOWN, root_cause="not enough evidence", cited_evidence_ids=[]),
        EVIDENCE,
    )
    assert report.ok is True


def test_unknown_cause_citing_a_ghost_fails() -> None:
    report = guard(
        verdict(cause=Cause.UNKNOWN, root_cause="x", cited_evidence_ids=["ev_ghost"]), EVIDENCE
    )
    assert report.ok is False


def test_ev_token_in_root_cause_must_exist() -> None:
    report = guard(verdict(root_cause="caused by ev_log_777"), EVIDENCE)
    assert report.ok is False
    assert any(p.code == "missing_text_citation" for p in report.problems)


def test_ev_token_that_exists_is_fine() -> None:
    assert guard(verdict(root_cause="caused by ev_log_001"), EVIDENCE).ok is True


def test_problems_are_deduplicated() -> None:
    report = guard(
        verdict(cited_evidence_ids=["ev_gone", "ev_gone"], root_cause="ev_gone and ev_gone"),
        EVIDENCE,
    )
    # Two distinct rules fired, and each one is reported once despite repetition.
    assert sorted(p.code for p in report.problems) == [
        "missing_citation",
        "missing_text_citation",
    ]
