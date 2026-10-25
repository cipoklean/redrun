"""The eval's arithmetic: scoring, aggregation, and what "pending" means."""

import json

from redrun.core.models import ConfidenceSignal, Verdict
from redrun.core.taxonomy import Cause
from redrun.eval import metrics


def _verdict(cause: Cause, cited: list[str], confidence: float = 0.5) -> Verdict:
    return Verdict(
        cause=cause,
        root_cause="because",
        confidence=confidence,
        confidence_breakdown=[ConfidenceSignal(signal="link_score", weight=0.25, value=1.0)],
        next_step="do the thing",
        cited_evidence_ids=cited,
    )


def test_category_ok_is_an_exact_taxonomy_match():
    assert metrics.category_ok("bad_commit", "bad_commit")
    assert metrics.category_ok(" Bad_Commit ", "bad_commit")
    assert not metrics.category_ok("flaky_test", "bad_commit")
    assert not metrics.category_ok(None, "bad_commit")


def test_keywords_need_every_word_and_report_the_missing_one():
    ok, missing = metrics.keywords_ok("The packaging pin dropped LegacyVersion", ["packaging", "legacyversion"])
    assert ok and missing == []

    ok, missing = metrics.keywords_ok("The packaging pin moved", ["packaging", "legacyversion"])
    assert not ok and missing == ["legacyversion"]


def test_keywords_are_case_insensitive_and_survive_no_text():
    assert metrics.keywords_ok("HTTPCORE conflict", ["httpcore"])[0]
    assert metrics.keywords_ok(None, ["httpcore"])[1] == ["httpcore"]


def test_citation_ok_reads_the_guard():
    evidence = {"ev_log_001", "ev_file_002"}
    assert metrics.citation_ok(_verdict(Cause.BAD_COMMIT, ["ev_log_001"]), evidence) is True
    assert metrics.citation_ok(_verdict(Cause.BAD_COMMIT, ["ev_nope"]), evidence) is False
    assert metrics.citation_ok(_verdict(Cause.BAD_COMMIT, []), evidence) is False
    assert metrics.citation_ok(_verdict(Cause.UNKNOWN, []), evidence) is True
    assert metrics.citation_ok(None, evidence) is None


def test_guard_intervention_is_read_from_the_trace(tmp_path):
    trace = tmp_path / "abc.jsonl"
    quiet = {"agent": "investigator", "type": "check", "message": "step 3 ok: hypotheses=1"}
    loud = {
        "agent": "investigator",
        "type": "check",
        "message": "citation guard rejected the verdict: no_citation",
    }
    trace.write_text("\n".join(json.dumps(item) for item in (quiet, loud)) + "\n", encoding="utf-8")
    assert metrics.guard_intervened(trace) is True

    trace.write_text(json.dumps(quiet) + "\n", encoding="utf-8")
    assert metrics.guard_intervened(trace) is False


def test_guard_intervention_survives_a_missing_or_broken_trace(tmp_path):
    assert metrics.guard_intervened(tmp_path / "nope.jsonl") is False
    assert metrics.guard_intervened(None) is False
    broken = tmp_path / "broken.jsonl"
    broken.write_text("not json\n", encoding="utf-8")
    assert metrics.guard_intervened(broken) is False


def test_percentile_is_nearest_rank():
    assert metrics.percentile([], 0.5) is None
    assert metrics.percentile([10], 0.95) == 10
    assert metrics.percentile([10, 20, 30, 40], 0.5) == 20
    assert metrics.percentile([10, 20, 30, 40], 0.95) == 40


def _row(**overrides) -> dict:
    row = {
        "id": "case",
        "split": "dev",
        "expected_cause": "bad_commit",
        "cause": "bad_commit",
        "category_ok": True,
        "keywords_ok": True,
        "confidence": 0.8,
        "citation_ok": True,
        "guard_intervened": False,
        "latency_ms": 1000,
        "tokens_in": 100,
        "tokens_out": 20,
        "path": "pipeline",
    }
    row.update(overrides)
    return row


def test_summarise_counts_accuracy_calibration_and_cost():
    rows = [
        _row(),
        _row(id="b", category_ok=False, keywords_ok=False, confidence=0.9, cause="flaky_test", latency_ms=3000),
        _row(id="c", citation_ok=None, latency_ms=2000, guard_intervened=True),
    ]
    summary = metrics.summarise(rows, system="agent")
    assert summary["cases"] == 3
    assert (summary["cause_hits"], summary["cause_accuracy"]) == (2, 0.6667)
    assert summary["keyword_hits"] == 2
    assert summary["citation_pass"] == 2 and summary["citation_evaluated"] == 2
    assert summary["guard_interventions"] == 1
    assert summary["latency_p50_ms"] == 2000 and summary["latency_p95_ms"] == 3000
    assert summary["tokens_in"] == 300 and summary["tokens_out"] == 60
    assert summary["calibration"] == {"correct": 0.8, "wrong": 0.9, "correct_cases": 2, "wrong_cases": 1}
    assert summary["confusion"]["bad_commit"] == {"bad_commit": 2, "flaky_test": 1}


def test_summarise_of_nothing_is_empty_not_wrong():
    summary = metrics.summarise([], system="agent")
    assert summary["cases"] == 0
    assert summary["cause_accuracy"] is None
    assert summary["citation_pass"] is None
    assert summary["calibration"]["correct"] is None
    assert summary["confusion"] == {}


def test_pending_lists_every_case_the_pipeline_did_not_run():
    rows = [
        {"id": "a", "split": "dev", "path": "pipeline"},
        {
            "id": "b",
            "split": "holdout",
            "path": "rules_pending_llm",
            "path_reason": "no recorded LLM step",
        },
    ]
    pending = metrics.pending(rows)
    assert [item["id"] for item in pending] == ["b"]
    assert pending[0]["reason"] == "no recorded LLM step"
