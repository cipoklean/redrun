"""World model: every fact carries provenance, and no fact exists without evidence."""

import json
from pathlib import Path

import pytest

from redrun.collectors import deps as deps_collector
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Evidence
from redrun.world.context import ContextBuilder
from redrun.world.ingest import ingest
from redrun.world.store import ENTITY_KINDS, Fact, WorldModel, WorldStoreService

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def collected(sample: str = "dependency_bump"):
    ctx = CollectContext(
        settings=Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=ROOT / "logs"),
        sample=sample,
    )
    runs = github_runs.collect(ctx)
    changes = diff_collector.collect(ctx, runs)
    job_logs = logs.collect(ctx, runs)
    deps = deps_collector.collect(ctx, runs, changes, job_logs)
    evidence = runs.evidence + changes.evidence + job_logs.evidence + deps.evidence
    return runs, changes, job_logs, deps, evidence


def world_for(sample: str = "dependency_bump", trace_id: str = "t1") -> WorldModel:
    runs, changes, job_logs, deps, evidence = collected(sample)
    return ingest(
        trace_id=trace_id,
        runs=runs,
        changes=changes,
        job_logs=job_logs,
        deps=deps,
        links=[],
        evidence=evidence,
    )


# ---------------------------------------------------------------- store


def test_fact_without_evidence_is_rejected() -> None:
    with pytest.raises(ValueError):
        Fact(
            fact_id="f1",
            subject="run:1",
            predicate="conclusion",
            object="failure",
            evidence_id="",
            collector="github_runs",
            source_url="https://example.test",
        )


def test_store_writes_world_json_under_the_trace(tmp_path: Path) -> None:
    world = world_for()
    path = WorldStoreService(tmp_path).save(world)
    assert path == tmp_path / "t1" / "world.json"
    payload = json.loads(path.read_text())
    assert payload["trace_id"] == "t1"
    assert payload["facts"]


def test_store_round_trips(tmp_path: Path) -> None:
    world = world_for()
    service = WorldStoreService(tmp_path)
    service.save(world)
    loaded = service.load("t1")
    assert loaded == world


def test_entities_cover_the_documented_kinds() -> None:
    world = world_for()
    kinds = {entity.kind for entity in world.entities}
    assert {"run", "commit", "file", "package", "log_error"} <= kinds
    assert kinds <= set(ENTITY_KINDS)


def test_every_fact_points_at_a_real_entity_and_evidence() -> None:
    world = world_for()
    entity_ids = {entity.entity_id for entity in world.entities}
    evidence_ids = {e.id for e in collected()[4]}
    for fact in world.facts:
        assert fact.subject in entity_ids, fact.subject
        assert fact.evidence_id in evidence_ids, fact.evidence_id


def test_ingest_is_deterministic() -> None:
    assert world_for() == world_for()


def test_facts_are_queryable_by_evidence() -> None:
    world = world_for()
    service = WorldStoreService(Path("/tmp"))
    subset = service.facts_for(world, ["ev_dep_fastapi"])
    assert subset
    assert all(fact.evidence_id == "ev_dep_fastapi" for fact in subset)


# ---------------------------------------------------------------- context


def test_context_carries_only_its_own_chunk() -> None:
    world = world_for()
    _, _, _, _, evidence = collected()
    builder = ContextBuilder(world, evidence)

    logs_ctx = builder.build_chunk_context("logs", {}, budget_tokens=3000)
    deps_ctx = builder.build_chunk_context("diff_deps", {}, budget_tokens=3000)

    assert logs_ctx.evidence_ids
    assert all(eid.startswith("ev_log") for eid in logs_ctx.evidence_ids)
    assert all(eid.startswith(("ev_file", "ev_commit", "ev_dep")) for eid in deps_ctx.evidence_ids)
    assert not set(logs_ctx.evidence_ids) & set(deps_ctx.evidence_ids)


def test_context_respects_the_token_budget() -> None:
    world = world_for()
    _, _, _, _, evidence = collected()
    builder = ContextBuilder(world, evidence)
    for budget in (120, 400, 3000):
        ctx = builder.build_chunk_context("diff_deps", {}, budget_tokens=budget)
        assert ctx.tokens_estimate <= budget, (budget, ctx.tokens_estimate)


def test_carryover_is_not_evidence() -> None:
    world = world_for()
    _, _, _, _, evidence = collected()
    builder = ContextBuilder(world, evidence)
    ctx = builder.build_chunk_context(
        "links", {"hypotheses": [{"cause": "bad_commit", "claim": "x", "supporting_ids": []}]}, 3000
    )
    assert "bad_commit" not in json.dumps([e for _, e in ctx.excerpts])


def test_context_excerpts_are_tagged_with_evidence_ids() -> None:
    world = world_for()
    _, _, _, _, evidence = collected()
    builder = ContextBuilder(world, evidence)
    ctx = builder.build_chunk_context("logs", {}, 3000)
    known = {e.id for e in evidence}
    for evidence_id, excerpt in ctx.excerpts:
        assert evidence_id in known
        assert excerpt


def test_unknown_chunk_is_rejected() -> None:
    world = world_for()
    _, _, _, _, evidence = collected()
    builder = ContextBuilder(world, evidence)
    with pytest.raises(ValueError):
        builder.build_chunk_context("nonsense", {}, 100)


def test_estimate_tokens_is_chars_over_four() -> None:
    from redrun.world.context import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100
