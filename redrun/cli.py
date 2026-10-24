"""CLI: investigate a failed run, or check the Zetaris wiring.

    python -m redrun investigate <url-or-owner/repo> [--sample name] [--engine duckdb|zetaris] [--json]
    python -m redrun zetaris-check [--sample dependency_bump]
"""

import argparse
import sys

from redrun.adapters.base import EngineError
from redrun.adapters.zetaris import ZetarisEngine
from redrun.collectors.base import CollectorError
from redrun.config import Settings, load_settings
from redrun.core.models import RunResult
from redrun.pipeline import investigate, prepare, run_overlap

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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "zetaris-check":
        return _zetaris_check(args.sample, load_settings())

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
