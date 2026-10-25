# RedRun

Why did this GitHub Actions run fail? RedRun answers with a cause category, a
root-cause explanation, cited evidence, a confidence score it can justify, and
one next step.

Built for the Open Agent Hackathon 2026, Track 2 (The Agent That Can Explain Why).

## What works right now

- Deterministic collectors: the failed run, the last green run of the same
  workflow on the same branch, the commit/file diff between them, error windows
  from the failed job logs, and PyPI release timing for changed dependencies.
- Secret redaction before anything is stored, traced, cached or sent to a model.
- Four evidence tables per run (`data/<trace_id>/`, Parquet and CSV): `runs`,
  `changes`, `log_errors`, `dependencies`.
- **One cross-source SQL query** (`redrun/queries/overlap.sql`) joins them into
  ranked links: a log error to a dependency released between the runs, to a
  changed file, to a workflow change, or a run-level config difference.
- That query runs on **Zetaris** when configured, on **DuckDB** otherwise, and on
  recorded results in sample mode. `metrics.query_engine` always says which.
- A **Markovian investigator** that reads the evidence in ordered chunks: each
  LLM call sees one chunk plus a capped carryover state, never the whole run.
  `metrics.markovian` reports steps, stop reason, tokens in, the token cost of a
  single all-evidence prompt, and `tokens_saved`.
- A **skeptic** that runs six tool checks on the proposed verdict
  (same-SHA-elsewhere, test history, error/change overlap, GitHub incidents,
  dependency timing, citation validity). A decisive failure sends one critique
  back to the investigator for exactly one revision; a second failure ends in
  `unknown` with the reason.
- A **citation guard**: a verdict may only cite evidence ids that exist in that
  run, and a non-`unknown` cause must cite at least one.
- **Computed confidence** from named signals in `confidence_breakdown`; the model
  never emits a score, and any it returns is stripped and logged.
- **Live events**: every stage writes to the JSONL trace and to an SSE stream.
- **An evaluation set of 15 real cases**, scored against a plain-model baseline,
  replayed offline: `python -m redrun eval --split all --mode replay --baseline`.
- CLI and API, four offline sample cases, and a recorder for new ones.

## The evidence engines

| situation | engine | `metrics.query_engine` |
| --- | --- | --- |
| `SAMPLE_MODE=true` and a sample selected | recorded result replayed | `zetaris_replay` |
| `ZETARIS_ENABLED=true` with credentials | Zetaris, DuckDB on error | `zetaris` or `duckdb_fallback` |
| anything else, or `--engine duckdb` | local DuckDB over the Parquet files | `duckdb` |
| `--engine zetaris` | Zetaris, no fallback: errors surface | `zetaris` |

Replay is labelled `zetaris_replay`, never `zetaris`, so a recording is never
mistaken for a live query.

## Zetaris

```bash
cp .env.example .env    # fill ZETARIS_URL + ZETARIS_TOKEN, or ZETARIS_USER/ZETARIS_PASSWORD
python -m redrun zetaris-check --sample dependency_bump
```

`zetaris-check` prints the status, runs `SELECT 1`, loads the four tables, runs
`overlap.sql`, and points at the trace. With no credentials it prints `offline`
and exits 1 rather than reporting a query it did not run.

Every endpoint the adapter uses is in one constants block in
`adapters/zetaris.py`, each line naming the first-party file it was confirmed
against. There is one TODO there: no confirmed Zetaris endpoint registers a
local Parquet file as a datasource, so in-place registration raises
`NotConfigured` and the adapter loads rows into tables through the confirmed
SQL-run endpoint instead (method `loaded_table`, recorded in the trace).

## Quickstart (no credentials, no network)

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

pytest
SAMPLE_MODE=true python -m redrun investigate --sample dependency_bump
SAMPLE_MODE=true python -m redrun investigate --sample bad_commit --json
```

Sample cases: `dependency_bump`, `bad_commit`, `expired_or_missing_secret`.
Each one writes `logs/<trace_id>.jsonl`.

## API

```bash
SAMPLE_MODE=true uvicorn redrun.api.app:app --port 8000

curl -s localhost:8000/health
curl -s localhost:8000/provider/status
curl -s -X POST localhost:8000/run \
  -H 'content-type: application/json' \
  -d '{"sample":"expired_or_missing_secret"}'

# the same run, as it happens
curl -sN -X POST localhost:8000/run/stream \
  -H 'content-type: application/json' -d '{"sample":"flaky_test"}'

# replay a finished run's trace
curl -sN localhost:8000/run/<trace_id>/events
```

## Live mode

```bash
cp .env.example .env      # then fill GITHUB_TOKEN
SAMPLE_MODE=false python -m redrun investigate https://github.com/owner/repo/actions/runs/123456
SAMPLE_MODE=false python -m redrun investigate owner/repo    # latest failed run on the default branch
```

The LLM provider is chosen by `LLM_PROVIDER`: `meterless` (default) or `nvidia`.
`GET /provider/status` reports `online`, `unauthorized` or `offline` without
throwing.

Qualify the runtime itself, then record a new sample from a real run:

```bash
python -m redrun meterless-check            # GET /v1/models + one pinned completion
python -m redrun record-sample <run-url> --name my_case
```

`meterless-check` masks the token, prints the model the runtime actually
returned, and exits 1 naming `offline` or `unauthorized` separately. It never
falls back silently: if a different provider serves the completion, it says so.
`record-sample` writes a redacted sample directory (collector outputs, the
Zetaris result with `recorded_from`, the skeptic inputs and every LLM step) so
`SAMPLE_MODE=true` can replay it with no network.

## Docker

```bash
cp .env.example .env
docker compose up --build
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d '{"sample":"bad_commit"}'
```

Traces land in `./logs`. Inside the container the Meterless runtime is reached at
`http://host.docker.internal:8088/v1`; compose sets that for you.

## The evaluation

`eval/cases.yaml` is the set: 15 cases, each with the run URL, the cause it was
built to have, and two to four words a correct explanation has to contain. Twelve
are constructed in [cipoklean/redrun-lab](https://github.com/cipoklean/redrun-lab),
a tiny package whose branches each carry a green run followed by a real breaking
commit: a loosened pin, a resolver conflict, a logic bug, a rename, a syntax error,
two flakes, a missing secret, an invalid token, a Python version, an env var and a
working directory. Two are public runs that line up with a GitHub Actions incident
and later passed on the same sha; one is a public run with no usable signal, where
`unknown` is the right answer.

The split was drawn from the seed in the file before anything was run, and
`tests/test_eval_cases.py` recomputes that draw, so the holdout cannot move after
its results are known.

`redrun/eval/baseline.py` is the comparison: one plain call on the same fast lane,
given only the last 300 redacted log lines and the cause taxonomy. No collectors,
no evidence tables, no cross-source query, no skeptic, no citations.

```bash
python -m redrun eval --split all --mode replay --baseline
```

Replay reads the recorded samples under `samples/eval_*`, so it needs no network
and no credentials. It writes `eval/results/<timestamp>.json` and `eval/RESULTS.md`
(both generated; do not edit the report by hand) with category accuracy, root-cause
keyword matches, citation validity, calibration, latency percentiles, tokens and a
confusion matrix, for dev, holdout and all, agent beside baseline. A case whose
model steps were never recorded is listed as pending and scored on nothing.

Files under `eval/lab/` are how the case set was built and can be re-run:
`build_lab.py` creates the runs in redrun-lab, `validate_lab.py` proves each
breaking commit really fails before anything is pushed, `find_public_cases.py`
searches incidents and public runs, `record_eval.py` records every case, and
`make_cases.py` writes `eval/cases.yaml` from the recorded run ids.

## The interface

The UI is React, Vite, TypeScript and Tailwind, with shadcn style components
vendored under `frontend/src/components/ui`. FastAPI serves the built app at `/`,
so one container is still the whole product.

```bash
cd frontend
npm install
npm run types     # regenerate src/types/contract from docs/schemas
npm run build     # writes frontend/dist
npm run dev       # hot reload on 5173, API proxied to 127.0.0.1:8001
```

If vite exits with a missing esbuild binary, npm skipped its postinstall script.
`npm rebuild esbuild` fixes it.

Three screens. `/` takes a run URL or an `owner/repo` pair, shows the provider
strip and the sample cards. `/eval` reads the newest results file and shows the
totals per split with the baseline beside the agent, then one row per case.
`/run/<trace_id>` streams the investigation live:
the vertical pipeline lights up stage by stage, every investigator step shows
its lane, model, tokens and carryover, the skeptic checks appear as pass, fail
or n/a chips, and a critique handoff is drawn as an arrow back to the
investigator. When the run finishes the same page becomes the verdict: cause
badge, root cause with clickable `ev_` chips, next step, the confidence gauge
with its full breakdown, evidence cards grouped by kind, a change timeline from
the last green run to the failed one, and a raw event log.

`src/types/contract/` is generated from `docs/schemas`, and `npm run
check:types` fails when the two drift.

## Tests

```bash
cd frontend
npx vitest run          # verdict rendering per sample, ev chip linking, confidence sums
npx playwright test     # smoke: run a sample, land on a verdict, click a citation
```

Tests are in `frontend/src/test` and `frontend/e2e`. Screenshots of the four
states live in `docs/screenshots`.

## Contract

`docs/contract.md` is the frozen wire format, `docs/schemas/` holds the exported
JSON Schemas, and `pytest` fails if either drifts from `redrun/core/models.py`.
