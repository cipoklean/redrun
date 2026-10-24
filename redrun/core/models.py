"""The wire contract. Frozen: the frontend and later agents build against these shapes."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from redrun.core.taxonomy import Cause

EvidenceKind = Literal["run", "commit", "file", "log", "dependency", "outage", "rerun"]
AgentName = Literal["triage", "collector", "zetaris", "investigator", "skeptic"]
EventType = Literal["start", "progress", "handoff", "check", "result", "error"]


class Evidence(BaseModel):
    """One citable fact. `excerpt` is always redacted before it is stored."""

    id: str
    kind: EvidenceKind
    source_url: str
    title: str
    excerpt: str
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfidenceSignal(BaseModel):
    """One measured signal behind the confidence number."""

    signal: str
    weight: float
    value: float


class Verdict(BaseModel):
    cause: Cause
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_breakdown: list[ConfidenceSignal]
    next_step: str
    cited_evidence_ids: list[str]


class StepInfo(BaseModel):
    number: int
    name: str
    conclusion: str


class JobInfo(BaseModel):
    job_id: int
    name: str
    conclusion: str
    steps: list[StepInfo] = Field(default_factory=list)


class RunInfo(BaseModel):
    run_id: int
    workflow_name: str
    workflow_path: str
    branch: str
    head_sha: str
    created_at: datetime
    conclusion: str
    runner_image: str | None = None
    jobs: list[JobInfo] = Field(default_factory=list)


class StepMetrics(BaseModel):
    """One LLM step of the Markovian investigator."""

    step: int
    chunk: str
    lane: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    ok: bool = True


class MarkovianMetrics(BaseModel):
    steps: int = 0
    stop_reason: Literal["rule", "cap", "error"] = "cap"
    tokens_in: int = 0
    tokens_out: int = 0
    carryover_tokens_max: int = 0
    full_context_tokens_estimate: int = 0
    tokens_saved: int = 0
    per_step: list[StepMetrics] = Field(default_factory=list)


class Metrics(BaseModel):
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    query_engine: str | None = None
    markovian: MarkovianMetrics | None = None


LinkType = Literal[
    "dependency_match",
    "file_match",
    "secret_or_config_match",
    "config_match",
]


class Link(BaseModel):
    """One cross-source join from overlap.sql: a log error tied to a change."""

    link_type: LinkType
    log_evidence_id: str | None = None
    change_evidence_id: str | None = None
    score_hint: float
    detail: str


class RunResult(BaseModel):
    run_id: str
    input: str
    failed_run: RunInfo
    last_green_run: RunInfo | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    verdict: Verdict | None = None
    links: list[Link] = Field(default_factory=list)
    trace_id: str
    metrics: Metrics = Field(default_factory=Metrics)


class Event(BaseModel):
    """One line of the JSONL trace. Also the SSE payload shape, later."""

    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: AgentName
    type: EventType
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


# One place that knows which models are published to docs/schemas/.
EXPORTED_SCHEMAS: dict[str, type[BaseModel]] = {
    "evidence": Evidence,
    "verdict": Verdict,
    "run_result": RunResult,
    "event": Event,
    "link": Link,
}
