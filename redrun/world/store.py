"""The world model: entities and provenance-carrying facts, persisted per trace."""

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, model_validator

EntityKind = Literal["run", "commit", "file", "package", "log_error", "outage", "test"]
ENTITY_KINDS: tuple[str, ...] = (
    "run",
    "commit",
    "file",
    "package",
    "log_error",
    "outage",
    "test",
)

FactObject = str | int | float | bool | None


class Entity(BaseModel):
    entity_id: str
    kind: EntityKind
    label: str
    evidence_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Fact(BaseModel):
    """One assertion. Provenance is not optional: no evidence, no fact."""

    fact_id: str
    subject: str
    predicate: str
    object: FactObject = None
    evidence_id: str
    collector: str
    source_url: str
    observed_at: datetime

    @model_validator(mode="after")
    def _require_evidence(self) -> "Fact":
        if not self.evidence_id or not self.evidence_id.strip():
            raise ValueError("a fact cannot exist without an evidence_id")
        return self


class WorldModel(BaseModel):
    trace_id: str
    entities: list[Entity] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


class WorldStoreService:
    """Persists one world per trace at data/<trace_id>/world.json."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path_for(self, trace_id: str) -> Path:
        return self.data_dir / trace_id / "world.json"

    def save(self, world: WorldModel) -> Path:
        path = self.path_for(world.trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(world.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def load(self, trace_id: str) -> WorldModel:
        return WorldModel.model_validate_json(self.path_for(trace_id).read_text(encoding="utf-8"))

    @staticmethod
    def facts_for(world: WorldModel, evidence_ids: Iterable[str]) -> list[Fact]:
        wanted = set(evidence_ids)
        return [fact for fact in world.facts if fact.evidence_id in wanted]
