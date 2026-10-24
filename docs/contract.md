# RedRun contract (frozen)

The frontend builds against this. Changes here are breaking changes.

All money-free, all JSON. Timestamps are ISO-8601 UTC.

## Evidence

One citable fact. `excerpt` is **already redacted** before it is stored, traced
or sent to a model.

| field | type | notes |
| --- | --- | --- |
| `id` | string | stable, derived from content (`ev_log_003`, `ev_commit_ab12cd`, `ev_file_007`) |
| `kind` | enum | `run`, `commit`, `file`, `log`, `dependency`, `outage`, `rerun` |
| `source_url` | string | a link a human can open to check the claim |
| `title` | string | one line, for a list row |
| `excerpt` | string | redacted text, patches capped at 200 lines |
| `timestamp` | string or null | ISO-8601 |
| `metadata` | object | kind-specific facts (see below) |

`metadata` by kind:

- `run` — `run_id`, `head_sha`, `conclusion`, `workflow`, `branch`, `runner_image`, `failing_steps`
- `commit` — `sha`, `author`, `message`
- `file` — `filename`, `status`, `additions`, `deletions`, `patch_lines`
- `log` — `job`, `job_id`, `line`, `patterns`, `tail` (boolean), and `lines` on the tail item

## Verdict

| field | type | notes |
| --- | --- | --- |
| `cause` | enum | `bad_commit`, `dependency_bump`, `flaky_test`, `infra_outage`, `expired_or_missing_secret`, `config_change`, `unknown` |
| `root_cause` | string | what happened, quoting the evidence |
| `confidence` | number 0..1 | computed from signals, never asked of a model |
| `confidence_breakdown` | array | `{signal, weight, value}`, one row per signal checked |
| `next_step` | string | exactly one recommended action |
| `cited_evidence_ids` | array of string | ids that must exist in `evidence` |

`confidence` is the sum of `weight * value` over the breakdown, capped at 0.95.
A cause scoring below 0.30 is reported as `unknown` instead.

## Link

One row of the cross-source query in `redrun/queries/overlap.sql`. This is what
ties a log error to a change.

| field | type | notes |
| --- | --- | --- |
| `link_type` | enum | `dependency_match`, `file_match`, `secret_or_config_match`, `config_match` |
| `log_evidence_id` | string or null | the log evidence the link starts from; null for run-level config links |
| `change_evidence_id` | string or null | the file or dependency evidence it joins to |
| `score_hint` | number | the SQL's own ranking hint, 0..1; not a probability |
| `detail` | string | why the two rows are joined |

## RunResult

| field | type | notes |
| --- | --- | --- |
| `run_id` | string | the failed run id |
| `input` | string | the URL or slug as given, or `sample:<name>` |
| `failed_run` | RunInfo | |
| `last_green_run` | RunInfo or null | same workflow, same branch, earlier |
| `evidence` | array of Evidence | unique ids |
| `verdict` | Verdict or null | null only if the pipeline could not run |
| `links` | array of Link | from the cross-source query, ranked by `score_hint` |
| `trace_id` | string | filename stem of `logs/<trace_id>.jsonl` |
| `metrics` | object | `llm_calls`, `tokens_in`, `tokens_out`, `latency_ms`, `query_engine`, `markovian` |
| `skeptic` | SkepticReport or null | tool checks, revisions, final status |

`query_engine` is one of `zetaris`, `zetaris_replay`, `duckdb`, `duckdb_fallback`.
`zetaris_replay` means a recorded result was replayed, never that a live query
ran. `duckdb_fallback` means the configured engine failed and is reported in the
trace as an error event.

## SkepticReport

| field | type | notes |
| --- | --- | --- |
| `checks` | array of CheckResult | six tool checks |
| `revisions` | number | 0 or 1; the loop is capped at one revision |
| `final_status` | enum | `passed`, `revised`, `failed`, `skipped` |

`CheckResult`: `check`, `status` (`pass` / `fail` / `n/a`), `detail`,
`evidence_ids`. `status` means "does this check support the proposed cause":
`pass` supports it, `fail` contradicts it, `n/a` does not apply. A `fail` on a
decisive check (`same_sha_elsewhere`, `test_failure_history`,
`error_change_overlap`, `outage_overlap`, `dependency_timing`,
`citation_validity`) sends the investigator back for exactly one revision. If
the re-check still fails, the cause becomes `unknown` and `root_cause` says why.

## Metrics.markovian

| field | type | notes |
| --- | --- | --- |
| `steps` | number | LLM calls made by the investigator, including a revision |
| `stop_reason` | enum | `rule` (evidence sufficient), `cap` (step limit) or `error` |
| `tokens_in` | number | prompt tokens actually sent |
| `tokens_out` | number | completion tokens |
| `carryover_tokens_max` | number | largest carryover state passed between steps |
| `full_context_tokens_estimate` | number | one prompt containing all evidence |
| `tokens_saved` | number | full estimate minus tokens actually sent, floored at 0 |
| `per_step` | array | `step`, `chunk`, `lane`, `provider`, `model`, `tokens_in`, `tokens_out`, `latency_ms`, `ok` |

`RunInfo`: `run_id` (number), `workflow_name`, `workflow_path`, `branch`,
`head_sha`, `created_at`, `conclusion`, `runner_image` (string or null), `jobs`.
`JobInfo`: `job_id`, `name`, `conclusion`, `steps`.
`StepInfo`: `number`, `name`, `conclusion`.

## Event (JSONL trace, and the SSE payload later)

| field | type | notes |
| --- | --- | --- |
| `ts` | string | ISO-8601 UTC |
| `agent` | enum | `triage`, `collector`, `zetaris`, `investigator`, `skeptic` |
| `type` | enum | `start`, `progress`, `handoff`, `check`, `result`, `error` |
| `message` | string | redacted, human readable |
| `data` | object | redacted |

Every run appends one Event per line to `logs/<trace_id>.jsonl`.

## Example (abridged)

```json
{
  "run_id": "5001",
  "input": "sample:dependency_bump",
  "failed_run": {
    "run_id": 5001,
    "workflow_name": "CI",
    "branch": "main",
    "head_sha": "3f9c2a1d4b5e6f708192a3b4c5d6e7f8091a2b3c",
    "conclusion": "failure",
    "runner_image": "ubuntu-24.04",
    "jobs": []
  },
  "last_green_run": { "run_id": 4980, "conclusion": "success" },
  "evidence": [
    {
      "id": "ev_log_001",
      "kind": "log",
      "source_url": "https://github.com/acme/widgets/actions/runs/5001/job/90011",
      "title": "dependency_resolution at line 17 in test (3.11)",
      "excerpt": "error: Failed to prepare distributions ...",
      "timestamp": "2026-10-23T09:14:02Z",
      "metadata": { "job": "test (3.11)", "line": 17, "tail": false }
    }
  ],
  "verdict": {
    "cause": "dependency_bump",
    "root_cause": "The dependency graph cannot be resolved between b71e0d9 and 3f9c2a1: ...",
    "confidence": 0.8,
    "confidence_breakdown": [
      { "signal": "log_resolution_conflict", "weight": 0.5, "value": 1.0 },
      { "signal": "log_module_not_found", "weight": 0.45, "value": 0.0 },
      { "signal": "diff_dependency_manifest_changed", "weight": 0.3, "value": 1.0 }
    ],
    "next_step": "Re-pin the bump in pyproject.toml so the resolver finds a consistent set, then re-run.",
    "cited_evidence_ids": ["ev_log_001", "ev_file_001"]
  },
  "trace_id": "b3f1c2d4e5a6",
  "metrics": { "llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "latency_ms": 12 }
}
```

Machine-readable versions of Evidence, Verdict, Link, RunResult and Event are in
`docs/schemas/`. A test asserts they match the models, so a stale export fails CI.

## Endpoints

- `POST /run {target?, sample?, engine?}` -> RunResult. `engine` is `zetaris` or
  `duckdb`; omit it to auto-select.
- `POST /run/stream` -> `text/event-stream` of Event objects, live as the run
  happens, then close. Errors arrive as `event: error`. The response carries
  `X-RedRun-Trace: <trace_id>` so the client can own its URL before the run ends.
- `GET /run/{trace_id}/events` -> the same Event stream, replayed from a
  finished trace. 404 for an unknown trace id.
- `GET /run/{trace_id}` -> the stored RunResult for a finished run. A browser
  navigation (Accept: text/html) gets the single page app instead, so client
  routes under /run keep working; `fetch` gets JSON.
- `GET /samples` -> `{samples: [{name, title, category, description, order}]}`
- `GET /provider/test` -> wraps the meterless and zetaris checks:
  `{llm: {provider, status, base_url, model_count, models, latency_ms, token_set},
  zetaris: {status, configured, enabled, url, latency_ms}, summary}`. Never
  returns a token value, only whether one is set.
- `GET /` and any non API path -> `frontend/dist/index.html` when the UI is built.
- `GET /health` -> `{status, sample_mode, version}`
- `GET /provider/status` -> `{llm: {provider, status}, zetaris: {status, configured, enabled, url}}`

Events never carry raw logs or secrets: only redacted excerpts, ids, step
numbers, token counts and check statuses. Every LLM call emits a progress event
with `provider`, `model`, `lane`, `latency_ms`, `tokens_in` and `tokens_out`.
Each investigator step result also carries a small `carryover preview` (the top
two hypotheses and the open question count), which is what the UI shows.

## Stored results

Every finished run writes `data/<trace_id>/result.json` next to its trace, so a
refreshed page can read the verdict without re-running anything.

## CLI

```
python -m redrun investigate <url-or-owner/repo> [--sample NAME] [--engine zetaris|duckdb] [--json]
python -m redrun zetaris-check [--sample NAME]
python -m redrun meterless-check
python -m redrun record-sample <run-url> --name <case>
```
