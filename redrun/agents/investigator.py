"""Markovian investigator: ordered chunks, carryover-only state, strict JSON replies.

The point of the shape is that no step ever sees the whole history. Each prompt
carries one chunk's facts and excerpts plus a small carryover object, so the cost
per step stays flat as evidence grows.
"""

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from redrun.adapters.llm import LLMClient, Usage
from redrun.core.models import StepMetrics, MarkovianMetrics
from redrun.core.taxonomy import Cause
from redrun.trace import Tracer
from redrun.world.context import ChunkContext, ContextBuilder, estimate_tokens

AGENT = "investigator"
BATCH_SIZE = 3

SYSTEM_PROMPT = (
    "You are the investigator for a CI failure. You are shown ONE chunk of evidence "
    "plus the carryover state from earlier steps. Reply with strict JSON only:\n"
    '{"carryover": {"hypotheses": [{"cause": "<cause>", "claim": "...", '
    '"supporting_ids": ["ev_..."], "against_ids": ["ev_..."]}], '
    '"open_questions": ["..."], "ruled_out": ["<cause>"]}, '
    '"verdict": {"cause": "<cause>", "root_cause": "...", "next_step": "...", '
    '"cited_evidence_ids": ["ev_..."]}}\n'
    "Set verdict to null unless you can cite evidence you were shown. Causes: "
    "bad_commit, dependency_bump, flaky_test, infra_outage, "
    "expired_or_missing_secret, config_change, unknown. "
    "Never output a confidence number: the engine computes it from signals."
)


class Hypothesis(BaseModel):
    cause: Cause
    claim: str = ""
    supporting_ids: list[str] = Field(default_factory=list)
    against_ids: list[str] = Field(default_factory=list)


class Carryover(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    ruled_out: list[Cause] = Field(default_factory=list)

    def capped(self, max_tokens: int) -> "Carryover":
        """Keep the carryover small: it is the only state that survives a step."""
        trimmed = self.model_copy(deep=True)
        trimmed.hypotheses = trimmed.hypotheses[:6]
        trimmed.open_questions = trimmed.open_questions[:5]
        for item in trimmed.hypotheses:
            item.claim = item.claim[:200]
            item.supporting_ids = item.supporting_ids[:6]
            item.against_ids = item.against_ids[:6]
        while estimate_tokens(trimmed.model_dump_json()) > max_tokens and trimmed.hypotheses:
            trimmed.hypotheses.pop()
        while estimate_tokens(trimmed.model_dump_json()) > max_tokens and trimmed.open_questions:
            trimmed.open_questions.pop()
        return trimmed


class ProposedVerdict(BaseModel):
    cause: Cause
    root_cause: str
    next_step: str
    cited_evidence_ids: list[str] = Field(default_factory=list)


class StepReply(BaseModel):
    carryover: Carryover
    verdict: ProposedVerdict | None = None


@dataclass
class StepRecord:
    step: int
    chunk: str
    lane: str
    ok: bool
    attempts: int
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    provider: str = ""
    model: str = ""
    error: str | None = None


@dataclass
class InvestigatorResult:
    carryover: Carryover
    verdict: ProposedVerdict | None
    metrics: MarkovianMetrics
    steps: list[StepRecord] = field(default_factory=list)
    stripped_confidence: bool = False


class MarkovianInvestigator:
    def __init__(
        self,
        *,
        llm: LLMClient,
        tracer: Tracer,
        builder: ContextBuilder,
        evidence: list,
        max_steps: int = 6,
        chunk_tokens: int = 3000,
        carryover_tokens: int = 800,
    ):
        self.llm = llm
        self.tracer = tracer
        self.builder = builder
        self.evidence = evidence
        self.max_steps = max_steps
        self.chunk_tokens = chunk_tokens
        self.carryover_tokens = carryover_tokens
        self._carryover_max = 0

    # ------------------------------------------------------------------ planning

    def _plan(self) -> list[tuple[str, list[str] | None]]:
        logs = self.builder.build_chunk_context("logs", {}, self.chunk_tokens)
        log_ids = list(logs.evidence_ids)
        batches: list[list[str]] = [
            log_ids[index : index + BATCH_SIZE] for index in range(0, len(log_ids), BATCH_SIZE)
        ] or [[]]
        plan: list[tuple[str, list[str] | None]] = [("links", None)]
        plan.extend(("logs", batch) for batch in batches)
        plan.extend([("diff_deps", None), ("run_config", None)])
        return plan

    def _messages(
        self, ctx: ChunkContext, carryover: Carryover, *, synthesis: bool = False
    ) -> list[dict[str, str]]:
        payload: dict = {
            "chunk": ctx.chunk,
            "facts": [
                {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object,
                    "evidence_id": fact.evidence_id,
                }
                for fact in ctx.facts
            ],
            "excerpts": [
                {"evidence_id": evidence_id, "excerpt": excerpt}
                for evidence_id, excerpt in ctx.excerpts
            ],
            "carryover": carryover.model_dump(mode="json"),
        }
        if synthesis:
            payload["available_evidence"] = [
                {"id": item.id, "kind": item.kind, "title": item.title} for item in self.evidence
            ]
            payload["instruction"] = (
                "This is the synthesis step. Write the final verdict, citing only ids "
                "from available_evidence."
            )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]

    # ------------------------------------------------------------------ stepping

    def _parse(self, text: str) -> tuple[StepReply, bool]:
        data = json.loads(text)
        stripped = False
        if isinstance(data, dict):
            if "confidence" in data:
                data.pop("confidence")
                stripped = True
            verdict = data.get("verdict")
            if isinstance(verdict, dict) and "confidence" in verdict:
                verdict.pop("confidence")
                stripped = True
        reply = StepReply.model_validate(data)
        if reply.verdict is not None:
            known = {item.id for item in self.evidence}
            reply.verdict.cited_evidence_ids = [
                cited for cited in reply.verdict.cited_evidence_ids if cited in known
            ]
        return reply, stripped

    def _step(
        self,
        step_no: int,
        chunk: str,
        ctx: ChunkContext,
        carryover: Carryover,
        lane: str,
        *,
        synthesis: bool = False,
        messages: list[dict[str, str]] | None = None,
    ) -> tuple[bool, Carryover, ProposedVerdict | None, StepRecord]:
        messages = messages or self._messages(ctx, carryover, synthesis=synthesis)
        record = StepRecord(step=step_no, chunk=chunk, lane=lane, ok=False, attempts=0)
        self.tracer.emit(
            AGENT,
            "start",
            f"step {step_no} chunk={chunk} lane={lane}",
            {
                "step": step_no,
                "chunk": chunk,
                "lane": lane,
                "context_tokens_estimate": ctx.tokens_estimate,
                "truncated": ctx.truncated,
            },
        )

        while record.attempts < 2:
            record.attempts += 1
            completion = self.llm.complete(messages, lane=lane)
            usage: Usage = completion.usage
            record.tokens_in += usage.prompt_tokens or estimate_tokens(
                " ".join(message["content"] for message in messages)
            )
            record.tokens_out += usage.completion_tokens
            record.latency_ms += usage.latency_ms
            record.provider = usage.provider
            record.model = usage.model
            self.tracer.emit(
                AGENT,
                "progress",
                f"step {step_no} {usage.provider}/{usage.model} "
                f"tokens_in={usage.prompt_tokens} tokens_out={usage.completion_tokens}",
                {
                    "step": step_no,
                    "provider": usage.provider,
                    "model": usage.model,
                    "lane": lane,
                    "latency_ms": usage.latency_ms,
                    "tokens_in": usage.prompt_tokens,
                    "tokens_out": usage.completion_tokens,
                },
            )
            try:
                reply, stripped = self._parse(completion.text)
            except (ValueError, ValidationError) as exc:
                record.error = str(exc)
                self.tracer.emit(
                    AGENT,
                    "progress",
                    f"step {step_no}: invalid reply, retrying once",
                    {"step": step_no, "error": str(exc)[:200]},
                )
                messages = messages + [
                    {
                        "role": "user",
                        "content": f"That reply was invalid: {exc}. Return the JSON object only.",
                    }
                ]
                continue

            if stripped:
                self.tracer.emit(
                    AGENT,
                    "check",
                    "model returned a confidence field; stripped it (the engine computes confidence)",
                    {"step": step_no},
                )
                self.stripped_confidence = True

            record.ok = True
            record.error = None
            self.tracer.emit(
                AGENT,
                "result",
                f"step {step_no} ok: hypotheses={len(reply.carryover.hypotheses)} "
                f"verdict={'yes' if reply.verdict else 'no'}",
                {
                    "step": step_no,
                    "chunk": chunk,
                    "hypotheses": len(reply.carryover.hypotheses),
                    "has_verdict": reply.verdict is not None,
                },
            )
            return True, reply.carryover, reply.verdict, record

        self.tracer.emit(
            AGENT,
            "error",
            f"step {step_no} failed after {record.attempts} attempts",
            {"step": step_no, "error": (record.error or "")[:200]},
        )
        return False, carryover, None, record

    def _stop_rule(self, carryover: Carryover) -> bool:
        if carryover.open_questions or not carryover.hypotheses:
            return False
        top = max(
            carryover.hypotheses,
            key=lambda item: len(item.supporting_ids) - len(item.against_ids),
        )
        return len(set(top.supporting_ids)) >= 2 and not top.against_ids

    # --------------------------------------------------------------------- run

    def run(self, *, triage_cause: Cause | None = None) -> InvestigatorResult:
        self.stripped_confidence = False
        carryover = Carryover()
        if triage_cause is not None and triage_cause is not Cause.UNKNOWN:
            carryover.hypotheses.append(
                Hypothesis(cause=triage_cause, claim=f"triage candidate: {triage_cause.value}")
            )

        records: list[StepRecord] = []
        proposed: ProposedVerdict | None = None
        chunk_steps = self._plan()[: max(0, self.max_steps - 1)]
        stopped_by_rule = False
        step_no = 0

        for chunk, subset in chunk_steps:
            step_no += 1
            ctx = self.builder.build_chunk_context(
                chunk, carryover.model_dump(mode="json"), self.chunk_tokens, subset
            )
            ok, carryover, verdict, record = self._step(step_no, chunk, ctx, carryover, "fast")
            records.append(record)
            carryover = carryover.capped(self.carryover_tokens)
            self._carryover_max = max(
                self._carryover_max, estimate_tokens(carryover.model_dump_json())
            )
            if verdict is not None:
                proposed = verdict
            if not ok:
                return self._finish(records, carryover, proposed, "error")
            if self._stop_rule(carryover):
                stopped_by_rule = True
                break

        step_no += 1
        synth_ctx = ChunkContext(chunk="synthesis", evidence_ids=[item.id for item in self.evidence])
        ok, carryover, verdict, record = self._step(
            step_no, "synthesis", synth_ctx, carryover, "deep", synthesis=True
        )
        records.append(record)
        if ok and verdict is not None:
            proposed = verdict
        if not ok:
            return self._finish(records, carryover, proposed, "error")

        if proposed is None and carryover.hypotheses:
            top = carryover.hypotheses[0]
            proposed = ProposedVerdict(
                cause=top.cause,
                root_cause=top.claim or f"{top.cause.value} is the best-supported cause so far",
                next_step="Re-run the failing job after addressing the top hypothesis.",
                cited_evidence_ids=list(top.supporting_ids),
            )
        return self._finish(records, carryover, proposed, "rule" if stopped_by_rule else "cap")

    def revise(
        self,
        *,
        critique: str,
        failed_checks: list[dict],
        carryover: Carryover,
        prior: ProposedVerdict | None,
    ) -> InvestigatorResult:
        """Exactly one revision step, handed the critique and the failed checks."""
        payload = {
            "chunk": "revision",
            "carryover": carryover.model_dump(mode="json"),
            "prior_verdict": prior.model_dump(mode="json") if prior else None,
            "skeptic_critique": critique,
            "failed_checks": failed_checks,
            "available_evidence": [
                {"id": item.id, "kind": item.kind, "title": item.title} for item in self.evidence
            ],
            "instruction": (
                "The skeptic contradicted the prior verdict. Revise it. Changing the cause is "
                "expected when the critique is right. Cite only ids from available_evidence."
            ),
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]
        ctx = ChunkContext(chunk="revision", evidence_ids=[item.id for item in self.evidence])
        ok, updated, verdict, record = self._step(
            1, "revision", ctx, carryover, "deep", messages=messages
        )
        return self._finish([record], updated, verdict if ok else None, "cap" if ok else "error")

    def _finish(
        self,
        records: list[StepRecord],
        carryover: Carryover,
        proposed: ProposedVerdict | None,
        stop_reason: str,
    ) -> InvestigatorResult:
        tokens_in = sum(record.tokens_in for record in records)
        full_estimate = self.builder.full_context_tokens(self.evidence)
        metrics = MarkovianMetrics(
            steps=len(records),
            stop_reason=stop_reason,  # type: ignore[arg-type]
            tokens_in=tokens_in,
            tokens_out=sum(record.tokens_out for record in records),
            carryover_tokens_max=self._carryover_max,
            full_context_tokens_estimate=full_estimate,
            tokens_saved=max(0, full_estimate - tokens_in),
            per_step=[
                StepMetrics(
                    step=record.step,
                    chunk=record.chunk,
                    lane=record.lane,
                    provider=record.provider,
                    model=record.model,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    latency_ms=record.latency_ms,
                    ok=record.ok,
                )
                for record in records
            ],
        )
        return InvestigatorResult(
            carryover=carryover,
            verdict=proposed,
            metrics=metrics,
            steps=records,
            stripped_confidence=self.stripped_confidence,
        )
