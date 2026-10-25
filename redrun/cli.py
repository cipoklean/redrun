"""CLI: investigate a failed run, or check the Zetaris wiring.

    python -m redrun investigate <url-or-owner/repo> [--sample name] [--engine duckdb|zetaris] [--json]
    python -m redrun zetaris-check [--sample dependency_bump]
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from redrun.adapters.base import EngineError
from redrun.adapters.llm import LLMClient, LLMError
from redrun.adapters.zetaris import ZetarisEngine
from redrun.agents.investigator import MarkovianInvestigator
from redrun.agents.skeptic import Skeptic
from redrun.collectors.base import CollectorError
from redrun.config import Settings, load_settings
from redrun.core.models import RunResult, Verdict
from redrun.core.rules import rule_verdict
from redrun.core.taxonomy import Cause
from redrun.eval import baseline as baseline_module
from redrun.eval import cases as cases_module
from redrun.eval import runner as eval_runner
from redrun.pipeline import _triage_cause, investigate, prepare, run_overlap
from redrun.recording import (
    RecordingLLM,
    write_baseline_pending,
    write_llm_pending,
    write_sample,
)
from redrun.world.context import ContextBuilder
from redrun.world.ingest import ingest

ENGINES = ("zetaris", "duckdb")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="redrun", description="Explain a failed GitHub Actions run.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("investigate", help="explain a failed run")
    run.add_argument(
        "target",
        nargs="?",
        help="https://github.com/owner/repo/actions/runs/<id> or owner/repo",
    )
    run.add_argument("--sample", help="use a bundled fixture instead of GitHub (offline)")
    run.add_argument(
        "--engine", choices=ENGINES, help="force an evidence engine instead of auto-selecting"
    )
    run.add_argument("--json", action="store_true", dest="as_json", help="print the RunResult JSON")

    check = sub.add_parser("zetaris-check", help="report Zetaris status and run a query")
    check.add_argument("--sample", default="dependency_bump", help="sample whose tables to query")

    meterless = sub.add_parser(
        "meterless-check", help="prove the Meterless/Nightshift runtime answers a real completion"
    )
    meterless.add_argument("--json", action="store_true", dest="as_json")

    record = sub.add_parser("record-sample", help="record a live run as a replayable sample")
    record.add_argument("url", help="https://github.com/owner/repo/actions/runs/<id>")
    record.add_argument("--name", required=True, help="sample directory name")
    record.add_argument(
        "--baseline",
        action="store_true",
        help="also record one plain-model reply on the log tail (baseline.json)",
    )

    scoring = sub.add_parser("eval", help="score the agent and the baseline over the case set")
    scoring.add_argument("--split", choices=("dev", "holdout", "all"), default="all")
    scoring.add_argument("--mode", choices=("replay", "live"), default="replay")
    scoring.add_argument(
        "--baseline",
        action="store_true",
        dest="with_baseline",
        help="score the recorded baseline alongside the agent",
    )
    scoring.add_argument("--cases", help="path to the case file (default eval/cases.yaml)")
    scoring.add_argument("--out-dir", help="where to write results/<timestamp>.json")
    scoring.add_argument("--markdown", help="where to write the rendered report")
    return parser


def _human(result: RunResult) -> str:
    verdict = result.verdict
    lines = [f"RedRun  {result.input}"]
    failed = result.failed_run
    lines.append(
        f"  Failed run   #{failed.run_id} {failed.workflow_name} on {failed.branch} "
        f"@ {failed.head_sha[:7]}"
    )
    if result.last_green_run is not None:
        green = result.last_green_run
        lines.append(f"  Last green   #{green.run_id} @ {green.head_sha[:7]}")
    else:
        lines.append("  Last green   none found for this workflow and branch")
    lines.append(f"  Engine       {result.metrics.query_engine}")
    if result.links:
        for link in result.links:
            lines.append(f"  Link         {link.link_type:<24} {link.score_hint:.2f}")
    else:
        lines.append("  Link         none found across sources")
    if verdict is not None:
        lines.append(f"  Cause        {verdict.cause.value} (confidence {verdict.confidence:.2f})")
        lines.append(f"  Why          {verdict.root_cause}")
        lines.append(f"  Next step:   {verdict.next_step}")
        lines.append(
            f"  Evidence     {len(result.evidence)} items, "
            f"{len(verdict.cited_evidence_ids)} cited"
        )
    else:
        lines.append("  Cause        not determined")
    lines.append(f"  Trace        {result.trace_id}.jsonl")
    return "\n".join(lines)


def _zetaris_check(sample: str, base: Settings) -> int:
    prepared = prepare(None, sample, base)
    engine = ZetarisEngine(base)
    status = engine.status()

    print(f"zetaris status : {status}")
    print(f"zetaris url    : {base.zetaris_url or '(unset)'}")
    print(f"catalog        : {base.zetaris_catalog or '(none)'}")
    print(f"sample         : {sample}  tables at {base.data_dir / prepared.trace_id}")
    print(f"trace          : {prepared.tracer.path}")

    if status != "online":
        prepared.tracer.emit(
            "zetaris", "error", f"status={status}; no query attempted", {"engine": "zetaris"}
        )
        print("not online: refusing to report a query that did not run")
        return 1

    try:
        one = engine.query("SELECT 1")
        print(f"SELECT 1       : {one.rows} ({one.latency_ms}ms)")
        links, label, skipped = run_overlap(prepared, base, "zetaris", engine=engine)
    except EngineError as exc:
        print(f"zetaris query failed: {exc}")
        return 2

    print(f"overlap.sql    : engine={label} links={len(links)} skipped={skipped}")
    for link in links[:5]:
        print(f"  {link.link_type:<24} {link.score_hint:.2f}  {link.detail[:90]}")
    return 0


def _mask(token: str) -> str:
    if not token:
        return "(not set)"
    if len(token) <= 6:
        return "*" * len(token)
    return f"{token[:4]}{'*' * (len(token) - 6)}{token[-2:]}"


def meterless_check(base: Settings, *, transport=None) -> int:
    """GET /v1/models, then ONE pinned completion. Exit 1 with a reason if it cannot."""
    # This check exists to qualify a live runtime, so SAMPLE_MODE must not short-circuit it.
    base = replace(base, sample_mode=False)
    client = LLMClient(base, sample_case=None, transport=transport)
    status = client.status()

    print(f"provider     : {client.provider}")
    print(f"base url     : {base.meterless_base_url}")
    print(f"token        : {_mask(base.meterless_runtime_token)}")
    print(f"status       : {status}")

    if client.provider != "meterless":
        print(
            f"WARNING: LLM_PROVIDER={client.provider!r}. This is not the Meterless/Nightshift "
            "path; nothing below proves Nightshift works."
        )
    if status == "unauthorized":
        print("exit 1: unauthorized (missing or rejected METERLESS_RUNTIME_TOKEN)")
        return 1
    if status != "online":
        print(
            f"exit 1: offline (nothing answering at {base.meterless_base_url}; "
            "is Nightshift running?)"
        )
        return 1

    models = client.list_models()
    print(f"models       : {len(models)} from GET /v1/models")
    for model_id in models[:20]:
        print(f"  - {model_id}")

    try:
        completion = client.complete(
            [{"role": "user", "content": "Reply with the single word: ready"}], lane="fast"
        )
    except LLMError as exc:
        print(f"chat         : FAILED {exc}")
        return 1

    usage = completion.usage
    print(f"chat HTTP    : {client.last_status}")
    print(f"chat model   : {usage.model}")
    print(f"chat latency : {usage.latency_ms} ms")
    print(f"chat tokens  : in={usage.prompt_tokens} out={usage.completion_tokens}")
    print(f"chat text    : {completion.text.strip()[:80]}")
    print(f"pinned model : {base.meterless_fast_model}")
    if usage.provider != "meterless":
        print(f"WARNING: the completion was served by {usage.provider!r}, not meterless")
    if usage.model != base.meterless_fast_model:
        print(f"NOTE: provider reported model {usage.model!r}, not the pinned id")
    return 0


def record_sample(url: str, name: str, base: Settings, *, with_baseline: bool = False) -> int:
    """One live run, saved as a replayable sample. Needs credentials and network.

    If the model cannot be reached, the collectors and the skeptic tool checks are
    still recorded, with the LLM part marked pending. A sample that replays half a
    conversation would be a fake investigation, so partial LLM steps are never kept.
    """
    if base.sample_mode:
        print("record-sample needs SAMPLE_MODE=false and live credentials")
        return 1
    live = replace(base, sample_mode=False)
    prepared = prepare(url, None, live)
    engine = "zetaris" if live.zetaris_enabled else "duckdb"
    llm_pending: str | None = None
    llm_steps: list[dict[str, Any]] = []
    try:
        links, label, _skipped = run_overlap(prepared, live, engine)
        llm = RecordingLLM(LLMClient(live, sample_case=None))
        proposal: Any = None
        try:
            world = ingest(
                trace_id=prepared.trace_id,
                runs=prepared.runs,
                changes=prepared.changes,
                job_logs=prepared.job_logs,
                deps=prepared.deps,
                links=links,
                evidence=prepared.evidence,
            )
            investigator = MarkovianInvestigator(
                llm=llm,
                tracer=prepared.tracer,
                builder=ContextBuilder(world, prepared.evidence),
                evidence=prepared.evidence,
                max_steps=live.investigator_max_steps,
            )
            investigated = investigator.run(triage_cause=_triage_cause(links, prepared.job_logs))
            proposal = investigated.verdict
            llm_steps = llm.steps
        except LLMError as exc:
            llm_pending = str(exc)
            print(f"  LLM unavailable ({exc}); recording collectors and skeptic only")
            proposal = rule_verdict(
                prepared.evidence,
                prepared.runs.failed_run,
                prepared.runs.last_green_run,
                links,
            )
        skeptic = Skeptic(
            settings=live,
            tracer=prepared.tracer,
            runs=prepared.runs,
            changes=prepared.changes,
            job_logs=prepared.job_logs,
            deps=prepared.deps,
            links=links,
            evidence=prepared.evidence,
            sample_case=None,
            github=prepared.client,
        )
        if proposal is not None:
            skeptic.check(
                Verdict(
                    cause=proposal.cause,
                    root_cause=proposal.root_cause,
                    confidence=0.0,
                    confidence_breakdown=[],
                    next_step=proposal.next_step,
                    cited_evidence_ids=list(proposal.cited_evidence_ids),
                )
            )
    except (CollectorError, EngineError) as exc:
        print(f"record-sample failed: {exc}")
        return 2
    finally:
        if prepared.client is not None:
            prepared.client.close()

    path = write_sample(
        settings=base,
        name=name,
        prepared=prepared,
        links=links,
        engine_label=label,
        llm_steps=llm_steps,
        skeptic=skeptic,
    )
    if llm_pending:
        write_llm_pending(path, llm_pending, captured=len(llm.steps))
    if with_baseline:
        try:
            payload = baseline_module.record(
                live, path, baseline_module.tail_from_prepared(prepared)
            )
        except baseline_module.BaselineError as exc:
            write_baseline_pending(path, str(exc))
            print(f"  baseline not recorded: {exc}")
        else:
            print(f"  baseline recorded: {(payload.get('reply') or {}).get('cause')}")
    note = " (LLM pending)" if llm_pending else ""
    print(f"recorded {path} ({len(llm_steps)} LLM steps, {len(links)} links, engine={label}){note}")
    return 0


def eval_command(args: argparse.Namespace, base: Settings) -> int:
    """Score the agent — and optionally the baseline — over the case set."""
    try:
        case_set = cases_module.load(Path(args.cases) if args.cases else None)
    except cases_module.CaseError as exc:
        print(f"redrun eval: {exc}", file=sys.stderr)
        return 2

    print(f"case file   : {args.cases or cases_module.CASES_PATH}")
    print(f"cases       : {len(case_set)}  seed={case_set.seed}")
    for name in ("dev", "holdout"):
        selected = case_set.selected(name)
        print(f"  {name:<8} {len(selected)}  {', '.join(case.id for case in selected)}")
    print(f"mode        : {args.mode}  split={args.split}  baseline={args.with_baseline}")

    try:
        payload = eval_runner.run(
            case_set,
            split=args.split,
            mode=args.mode,
            settings=base,
            with_baseline=args.with_baseline,
            out_dir=Path(args.out_dir) if args.out_dir else None,
            markdown_path=Path(args.markdown) if args.markdown else None,
            on_case=lambda message: print(message, flush=True),
        )
    except (ValueError, CollectorError, EngineError, LLMError) as exc:
        print(f"redrun eval: {exc}", file=sys.stderr)
        return 2

    for name, summary in payload["summaries"].items():
        agent, other = summary["agent"], summary["baseline"]
        line = (
            f"{name:<8} agent cause {agent['cause_hits']}/{agent['cases']} "
            f"keywords {agent['keyword_hits']}/{agent['cases']}"
        )
        if args.with_baseline:
            line += (
                f" | baseline cause {other['cause_hits']}/{other['cases']} "
                f"keywords {other['keyword_hits']}/{other['cases']}"
            )
        print(line)

    pending = payload["pending"]
    if pending:
        print("pending live recording: " + ", ".join(item["id"] for item in pending))
    else:
        print("pending live recording: none")
    print(f"wrote {eval_runner.RESULTS_DIR}/<timestamp>.json and {eval_runner.MARKDOWN_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "zetaris-check":
        return _zetaris_check(args.sample, load_settings())

    if args.command == "meterless-check":
        return meterless_check(load_settings())

    if args.command == "record-sample":
        return record_sample(
            args.url, args.name, load_settings(), with_baseline=args.baseline
        )

    if args.command == "eval":
        return eval_command(args, load_settings())

    if args.command != "investigate":
        _parser().print_help()
        return 2

    try:
        result = investigate(
            args.target, sample=args.sample, settings=load_settings(), engine=args.engine
        )
    except (ValueError, CollectorError, EngineError) as exc:
        print(f"redrun: {exc}", file=sys.stderr)
        return 2

    print(result.model_dump_json(indent=2) if args.as_json else _human(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
