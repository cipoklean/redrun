"""The metrics. Pure functions over observations, so every number is reproducible.

Nothing here calls a model, reads a sample or touches the pipeline. It reads the
verdicts and the recorded usage the runner hands it, and returns numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

from redrun.core.citations import guard
from redrun.core.models import Verdict

GUARD_REJECTION = "citation guard rejected the verdict"


def category_ok(predicted: str | None, expected: str) -> bool:
    """Exact match on the taxonomy. An unparsable reply is not a match."""
    return (predicted or "").strip().lower() == expected.strip().lower()


def keywords_ok(text: str | None, keywords: Iterable[str]) -> tuple[bool, list[str]]:
    """All keywords present, case-insensitive. Returns (ok, missing)."""
    haystack = (text or "").lower()
    missing = [word for word in keywords if word.lower() not in haystack]
    return (not missing), missing


def citation_ok(verdict: Verdict | None, evidence_ids: set[str]) -> bool | None:
    """Does the returned verdict satisfy the citation guard as it stands?"""
    if verdict is None:
        return None
    return guard(verdict, evidence_ids).ok


def guard_intervened(trace_path: Path | None) -> bool:
    """Did the pipeline's own guard throw a proposal away during this run?

    Read from the run's trace rather than inferred from the final verdict, so a
    guard rejection that happened and was recovered from is still counted.
    """
    if not trace_path or not Path(trace_path).exists():
        return False
    for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if GUARD_REJECTION in str(event.get("message", "")):
            return True
    return False


def percentile(values: list[int | float], fraction: float) -> float | None:
    """Nearest-rank percentile: the smallest value at or above `fraction` of the sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarise(observations: list[dict[str, Any]], *, system: str) -> dict[str, Any]:
    """Aggregate one system's observations: accuracy, calibration, cost, confusion."""
    total = len(observations)
    cause_hits = [row for row in observations if row["category_ok"]]
    keyword_hits = [row for row in observations if row["keywords_ok"]]
    latency = [row["latency_ms"] for row in observations if row.get("latency_ms")]
    tokens_in = [row.get("tokens_in") or 0 for row in observations]
    tokens_out = [row.get("tokens_out") or 0 for row in observations]

    with_confidence = [row for row in observations if row.get("confidence") is not None]
    correct_confidence = [row["confidence"] for row in with_confidence if row["category_ok"]]
    wrong_confidence = [row["confidence"] for row in with_confidence if not row["category_ok"]]

    citations = [row["citation_ok"] for row in observations if row.get("citation_ok") is not None]
    confusion: dict[str, dict[str, int]] = {}
    for row in observations:
        expected = row["expected_cause"]
        predicted = row.get("cause") or "none"
        confusion.setdefault(expected, {})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1

    return {
        "system": system,
        "cases": total,
        "cause_hits": len(cause_hits),
        "cause_accuracy": round(len(cause_hits) / total, 4) if total else None,
        "keyword_hits": len(keyword_hits),
        "keyword_accuracy": round(len(keyword_hits) / total, 4) if total else None,
        "citation_evaluated": len(citations),
        "citation_pass": sum(1 for item in citations if item) if citations else None,
        "citation_pass_rate": (
            round(sum(1 for item in citations if item) / len(citations), 4) if citations else None
        ),
        "guard_interventions": sum(1 for row in observations if row.get("guard_intervened")),
        "latency_p50_ms": percentile(latency, 0.50),
        "latency_p95_ms": percentile(latency, 0.95),
        "tokens_in": sum(tokens_in),
        "tokens_out": sum(tokens_out),
        "calibration": {
            "correct": _mean(correct_confidence),
            "wrong": _mean(wrong_confidence),
            "correct_cases": len(correct_confidence),
            "wrong_cases": len(wrong_confidence),
        },
        "confusion": confusion,
    }


def pending(observations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Cases the pipeline could not be measured on, and why. Never silently dropped."""
    out: list[dict[str, str]] = []
    for row in observations:
        if row.get("path") != "pipeline":
            out.append(
                {
                    "id": row["id"],
                    "split": row["split"],
                    "path": str(row.get("path") or "not run"),
                    "reason": str(row.get("path_reason") or "the agent pipeline did not run"),
                }
            )
    return out
