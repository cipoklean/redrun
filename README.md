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
- A rules-based verdict that scores those links alongside the log and diff
  signals. Every signal and weight is listed in `confidence_breakdown`.
- CLI and API, a JSONL trace per run, three offline sample cases.
- The LLM adapter is wired but **not called** by the verdict yet: the agents come
  in a later change.

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

## Docker

```bash
cp .env.example .env
docker compose up --build
curl -s -X POST localhost:8000/run -H 'content-type: application/json' -d '{"sample":"bad_commit"}'
```

Traces land in `./logs`. Inside the container the Meterless runtime is reached at
`http://host.docker.internal:8088/v1`; compose sets that for you.

## Contract

`docs/contract.md` is the frozen wire format, `docs/schemas/` holds the exported
JSON Schemas, and `pytest` fails if either drifts from `redrun/core/models.py`.
