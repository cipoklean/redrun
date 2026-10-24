"""Chunk context: only the facts and excerpts a chunk needs, inside a token budget."""

import json
from dataclasses import dataclass, field

from redrun.core.models import Evidence
from redrun.world.store import Fact, WorldModel

CHUNKS = ("links", "logs", "diff_deps", "run_config")

_PREFIXES = {
    "links": ("log:", "change:"),
    "logs": ("log:", "test:"),
    "diff_deps": ("commit:", "file:", "pkg:"),
    "run_config": ("run:", "outage:"),
}
# `links` must be selected by predicate: its facts hang off log: and file: subjects
# that other chunks own, and sharing them would leak a log excerpt into step one.
_LINK_PREDICATES = ("linked_via_", "link_score")


def estimate_tokens(text: str) -> int:
    """Deliberately crude: chars/4. The provider's usage replaces it when available."""
    return len(text) // 4


@dataclass
class ChunkContext:
    chunk: str
    facts: list[Fact] = field(default_factory=list)
    excerpts: list[tuple[str, str]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    tokens_estimate: int = 0
    truncated: bool = False

    def as_prompt_payload(self) -> dict:
        return {
            "chunk": self.chunk,
            "tokens_estimate": self.tokens_estimate,
            "facts": [
                {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object,
                    "evidence_id": fact.evidence_id,
                }
                for fact in self.facts
            ],
            "excerpts": [
                {"evidence_id": evidence_id, "excerpt": excerpt}
                for evidence_id, excerpt in self.excerpts
            ],
        }


class ContextBuilder:
    def __init__(self, world: WorldModel, evidence: list[Evidence]):
        self.world = world
        self.evidence = {item.id: item for item in evidence}

    def _facts_for(self, chunk: str) -> list[Fact]:
        if chunk == "links":
            return [
                fact
                for fact in self.world.facts
                if fact.predicate.startswith(_LINK_PREDICATES)
            ]
        prefixes = _PREFIXES[chunk]
        return [fact for fact in self.world.facts if fact.subject.startswith(prefixes)]

    def build_chunk_context(
        self,
        chunk: str,
        carryover: dict,
        budget_tokens: int,
        evidence_subset: list[str] | None = None,
    ) -> ChunkContext:
        """Facts first, then excerpts, both cut to fit `budget_tokens`."""
        if chunk not in CHUNKS:
            raise ValueError(f"unknown chunk {chunk!r}; expected one of {CHUNKS}")

        result = ChunkContext(chunk=chunk)
        used = estimate_tokens(json.dumps(carryover, default=str))
        allowed = set(evidence_subset) if evidence_subset is not None else None

        def keep(fact: Fact) -> bool:
            return allowed is None or fact.evidence_id in allowed

        for fact in self._facts_for(chunk):
            if not keep(fact):
                continue
            cost = estimate_tokens(json.dumps(fact.model_dump(mode="json"), default=str))
            if used + cost > budget_tokens:
                result.truncated = True
                break
            result.facts.append(fact)
            used += cost
            if fact.evidence_id not in result.evidence_ids:
                result.evidence_ids.append(fact.evidence_id)

        for evidence_id in list(result.evidence_ids):
            item = self.evidence.get(evidence_id)
            if item is None:
                continue
            remaining = budget_tokens - used
            if remaining <= 20:
                result.truncated = True
                break
            excerpt = item.excerpt
            max_chars = remaining * 4
            if len(excerpt) > max_chars:
                excerpt = excerpt[:max_chars]
                result.truncated = True
            result.excerpts.append((evidence_id, excerpt))
            used += estimate_tokens(excerpt)

        result.tokens_estimate = used
        return result

    def full_context_tokens(self, evidence: list[Evidence]) -> int:
        """What one prompt containing everything would have cost."""
        return sum(estimate_tokens(item.excerpt) for item in evidence)
