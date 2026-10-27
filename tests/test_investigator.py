"""Markovian investigator: chunked steps, carryover-only state, strict JSON."""

import json
from pathlib import Path

import pytest

from redrun.adapters.llm import Completion, Usage
from redrun.agents.investigator import MarkovianInvestigator
from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Evidence
from redrun.core.taxonomy import Cause
from redrun.trace import Tracer
from redrun.world.context import ContextBuilder
from redrun.world.ingest import ingest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


class ScriptedLLM:
    """Returns scripted replies in order and records every prompt it was given."""

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.prompts: list[list[dict]] = []
        self.lanes: list[str] = []

    def complete(self, messages, lane="fast"):
        self.prompts.append(messages)
        self.lanes.append(lane)
        index = min(len(self.prompts) - 1, len(self.replies) - 1)
        return Completion(
            text=self.replies[index],
            usage=Usage(
                prompt_tokens=100,
                completion_tokens=20,
                latency_ms=5,
                provider="fake",
                model=f"fake/{lane}",
            ),
        )


def build(sample: str = "dependency_bump"):
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    changes = diff_collector.collect(ctx, runs)
    job_logs = logs.collect(ctx, runs)
    deps = deps_collector.collect(ctx, runs, changes, job_logs)
    evidence: list[Evidence] = runs.evidence + changes.evidence + job_logs.evidence + deps.evidence
    world = ingest(
        trace_id="t1",
        runs=runs,
        changes=changes,
        job_logs=job_logs,
        deps=deps,
        links=[],
        evidence=evidence,
    )
    return world, evidence, ContextBuilder(world, evidence)


def reply(hypotheses=None, questions=None, verdict=None, confidence=None) -> str:
    payload: dict = {
        "carryover": {
            "hypotheses": hypotheses if hypotheses is not None else [],
            "open_questions": questions or [],
            "ruled_out": [],
        }
    }
    if verdict is not None:
        payload["verdict"] = verdict
        if confidence is not None:
            payload["verdict"]["confidence"] = confidence
    return json.dumps(payload)


def hypothesis(cause="dependency_bump", supporting=None, against=None) -> dict:
    return {
        "cause": cause,
        "claim": f"{cause} looks likely",
        "supporting_ids": supporting or [],
        "against_ids": against or [],
    }


def make(tmp_path: Path, llm, max_steps: int = 6) -> MarkovianInvestigator:
    world, evidence, builder = build()
    return MarkovianInvestigator(
        llm=llm,
        tracer=Tracer("i1", tmp_path),
        builder=builder,
        evidence=evidence,
        max_steps=max_steps,
    )


# ---------------------------------------------------------------- isolation


def test_each_step_prompt_contains_only_its_own_chunk(tmp_path: Path) -> None:
    llm = ScriptedLLM([reply([hypothesis()])])
    investigator = make(tmp_path, llm)
    investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)

    seen: list[tuple[str, str]] = []
    for messages in llm.prompts:
        text = json.dumps(messages)
        chunk = "logs" if "log_error" in text else ("diff_deps" if "pyproject.toml" in text else "other")
        seen.append((chunk, text))

    for chunk, text in seen:
        if chunk == "diff_deps":
            assert "no solution" not in text, "a diff step must not carry the log excerpt"


def test_later_steps_do_not_repeat_earlier_excerpts(tmp_path: Path) -> None:
    llm = ScriptedLLM([reply([hypothesis(supporting=["ev_log_001"])])])
    investigator = make(tmp_path, llm)
    investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)

    first_log_prompt = next(json.dumps(m) for m in llm.prompts if "no solution" in json.dumps(m))
    marker = "no solution"
    occurrences = [marker in json.dumps(prompt) for prompt in llm.prompts]
    assert occurrences.count(True) == 1, "one log excerpt should appear in exactly one step"
    assert first_log_prompt


def test_full_context_estimate_exceeds_tokens_sent(tmp_path: Path) -> None:
    llm = ScriptedLLM([reply([hypothesis()])])
    investigator = make(tmp_path, llm)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    assert result.metrics.full_context_tokens_estimate > 0
    assert result.metrics.tokens_saved > 0


# ---------------------------------------------------------------- control flow


def test_step_cap_is_respected(tmp_path: Path) -> None:
    llm = ScriptedLLM([reply([hypothesis()], questions=["still unsure"])])
    investigator = make(tmp_path, llm, max_steps=3)
    result = investigator.run(triage_cause=None)
    assert result.metrics.steps <= 3
    assert result.metrics.stop_reason == "cap"


def test_stop_rule_fires_on_two_supporting_ids_and_no_questions(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [reply([hypothesis(supporting=["ev_log_001", "ev_dep_fastapi"])])]
    )
    investigator = make(tmp_path, llm, max_steps=6)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    assert result.metrics.stop_reason == "rule"
    assert result.metrics.steps < 6


def test_invalid_json_is_retried_once_then_fails_the_step(tmp_path: Path) -> None:
    llm = ScriptedLLM(["not json at all", "still not json"])
    investigator = make(tmp_path, llm)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    assert result.metrics.stop_reason == "error"
    assert any(not step.ok for step in result.steps)
    assert len(llm.prompts) == 2, "one attempt plus exactly one retry"


def test_retry_recovers_when_the_second_reply_is_valid(tmp_path: Path) -> None:
    llm = ScriptedLLM(["broken", reply([hypothesis(supporting=["ev_log_001", "ev_dep_fastapi"])])])
    investigator = make(tmp_path, llm)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    assert result.steps[0].ok is True
    assert result.steps[0].attempts == 2


def test_confidence_from_the_model_is_stripped_and_logged(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            reply([hypothesis()]),
            reply(
                [hypothesis()],
                verdict={
                    "cause": "dependency_bump",
                    "root_cause": "fastapi pins httpx<0.28",
                    "next_step": "re-pin fastapi",
                    "cited_evidence_ids": ["ev_log_001"],
                },
                confidence=0.99,
            ),
        ]
    )
    investigator = make(tmp_path, llm)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)

    assert result.verdict is not None
    assert not hasattr(result.verdict, "confidence")
    assert result.stripped_confidence is True
    raw = (tmp_path / "i1.jsonl").read_text()
    assert "confidence" in raw and "stripped" in raw.lower()


def test_lanes_are_fast_for_chunks_and_deep_for_synthesis(tmp_path: Path) -> None:
    llm = ScriptedLLM([reply([hypothesis()])])
    investigator = make(tmp_path, llm)
    investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    assert llm.lanes[0] == "fast"
    assert llm.lanes[-1] == "deep"


def test_carryover_is_capped(tmp_path: Path) -> None:
    many = [hypothesis(supporting=[f"ev_log_{i:03d}"]) for i in range(1, 40)]
    llm = ScriptedLLM([reply(many, questions=["q"] * 30)])
    investigator = make(tmp_path, llm, max_steps=2)
    result = investigator.run(triage_cause=None)
    assert result.metrics.carryover_tokens_max <= investigator.carryover_tokens


def test_metrics_are_populated(tmp_path: Path) -> None:
    llm = ScriptedLLM([reply([hypothesis()])])
    investigator = make(tmp_path, llm)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    metrics = result.metrics
    assert metrics.steps == len(metrics.per_step) >= 1
    assert metrics.tokens_in > 0
    assert all(step.provider == "fake" for step in metrics.per_step)


def test_evidence_ids_in_verdict_are_validated_against_the_run(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            reply([hypothesis()]),
            reply(
                [hypothesis()],
                verdict={
                    "cause": "dependency_bump",
                    "root_cause": "x",
                    "next_step": "y",
                    "cited_evidence_ids": ["ev_does_not_exist"],
                },
            ),
        ]
    )
    investigator = make(tmp_path, llm)
    result = investigator.run(triage_cause=Cause.DEPENDENCY_BUMP)
    assert result.verdict is not None
    assert result.verdict.cited_evidence_ids == []


def test_reasoning_prose_around_the_json_object_still_parses() -> None:
    text = (
        "We are at the synthesis step. The commit {changed} requirements.txt.\n\n"
        '{"carryover": {"hypotheses": [], "open_questions": [], "ruled_out": []}, '
        '"verdict": {"cause": "config_change", "root_cause": "python pinned to 3.9", '
        '"next_step": "pin 3.11 again", "cited_evidence_ids": []}}'
    )
    data = MarkovianInvestigator._extract_json(text)
    assert data["verdict"]["cause"] == "config_change"


def test_prose_with_no_json_object_is_still_invalid() -> None:
    with pytest.raises(ValueError):
        MarkovianInvestigator._extract_json("I could not decide, see {the logs}.")
