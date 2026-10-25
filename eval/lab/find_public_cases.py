"""Find the public cases: real failed runs that line up with a GitHub incident,
and one run with no usable signal.

    python eval/lab/find_public_cases.py incidents      # list Actions incidents
    python eval/lab/find_public_cases.py outage         # candidates in incident windows
    python eval/lab/find_public_cases.py unknown        # cancelled/timeout candidates
    python eval/lab/find_public_cases.py verify <run_url>

Writes eval/lab/public_candidates.json. It only reads GitHub; nothing is changed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent / "public_candidates.json"
STATUS_PAGE = "https://www.githubstatus.com/api/v2"
USER_AGENT = "redrun-eval-research"

# Popular Python repositories with a lot of Actions traffic: the more runs, the
# more likely that an outage window contains one of them.
REPOS = (
    "home-assistant/core",
    "django/django",
    "pandas-dev/pandas",
    "scikit-learn/scikit-learn",
    "pallets/flask",
    "pytest-dev/pytest",
    "pydantic/pydantic",
    "encode/httpx",
    "psf/requests",
    "fastapi/fastapi",
    "python-poetry/poetry",
    "apache/airflow",
    "getsentry/sentry",
    "streamlit/streamlit",
    "Textualize/textual",
    "pola-rs/polars",
    "astral-sh/uv",
    "pypa/pip",
)

# A failure in one of these steps is the environment's fault, not the commit's.
SETUP_STEPS = (
    "set up job",
    "complete job",
    "checkout",
    "run actions/checkout",
    "post run actions/checkout",
    "setup python",
    "set up python",
    "post set up python",
    "initialize containers",
    "stop containers",
    "cache",
    "start runner",
)

INFRA_TEXT = re.compile(
    r"(?i)(the runner has received a shutdown signal|runner lost communication"
    r"|lost communication with the server|the hosted runner encountered an error"
    r"|no space left on device|out of memory|oom-kill|connection reset|econnreset"
    r"|etimedout|eai_again|connection refused|connection timed out"
    r"|temporary failure in name resolution|could not resolve host|tls handshake"
    r"|failed to connect to github\.com|unable to access .*github\.com"
    r"|could not connect to server|received an unexpected eof"
    r"|failed to download|download archive|service unavailable|bad gateway"
    r"|gateway timeout|the operation was canceled)"
)

# A loose pattern called a CodeQL finding ("Missing rate limiting") an outage, and
# a test's own timeout a runner problem. Neither is the environment's fault, so the
# pattern names only things a runner says about itself. "delays" incidents do not
# fail runs at all: they make them slow, so the critical windows come first.
CRITICAL_FIRST = ("critical", "major")


def token() -> str:
    value = os.environ.get("GITHUB_TOKEN")
    if value:
        return value
    for line in Path("~/.git-credentials").expanduser().read_text().splitlines():
        if "@github.com" in line:
            _, _, tail = line.partition("://")
            credentials, _, _ = tail.partition("@")
            _, _, secret = credentials.partition(":")
            if secret:
                return secret
    raise SystemExit("no GitHub token available")


def api(url: str, *, absolute: bool = False, tries: int = 4):
    """GET a URL. Retries on 429/5xx, since the research path is rate-limited."""
    target = url if absolute else "https://api.github.com" + url
    for attempt in range(tries):
        request = urllib.request.Request(
            target,
            headers={
                "Authorization": f"Bearer {token()}",
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                payload = json.loads(raw) if raw else {}
                return payload, response.headers
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429) or exc.code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            return {"error": exc.code, "body": exc.read().decode("utf-8", "replace")[:200]}, {}
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
    return {"error": "gave up"}, {}


def http_text(url: str) -> str:
    """GET a URL with the token. A bare path is read as an API path."""
    target = url if url.startswith("http") else f"https://api.github.com{url}"
    request = urllib.request.Request(
        target,
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return f"__http_{exc.code}__"
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"__error_{exc}__"


_CLIENT = None


def job_log(owner: str, repo: str, job_id: int) -> str:
    """A job log, through RedRun's own client.

    urllib drops the Authorization header across the redirect to the log host and
    gets a 401, which looks exactly like "this job has no log". The collector's
    client follows the redirect properly, and what matters here is what the
    collector will see, not what a naive fetch sees.
    """
    global _CLIENT
    from redrun.adapters.github import GitHubClient
    from redrun.collectors.base import CollectorError

    if _CLIENT is None:
        _CLIENT = GitHubClient(os.environ.get("GITHUB_TOKEN") or token())
    try:
        return _CLIENT.download_logs(owner, repo, job_id)
    except CollectorError as exc:
        return f"__error_{exc}__"


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"incidents": [], "outage": [], "unknown": []}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------- incidents


def action_incidents(state: dict) -> None:
    """Every resolved incident that touched the Actions component."""
    payload, _ = api(f"{STATUS_PAGE}/incidents.json", absolute=True)
    incidents = payload.get("incidents") if isinstance(payload, dict) else None
    if not incidents:
        print(f"no incidents available: {str(payload)[:200]}")
        return
    found = []
    for incident in incidents:
        names = [item.get("name", "") for item in incident.get("components", [])]
        if not any("Actions" in name for name in names):
            continue
        if incident.get("status") not in ("resolved", "postmortem"):
            continue
        if (incident.get("impact") or "") == "none":
            continue
        found.append(
            {
                "id": incident.get("id"),
                "name": incident.get("name"),
                "impact": incident.get("impact"),
                "status": incident.get("status"),
                "created_at": incident.get("created_at"),
                "resolved_at": incident.get("resolved_at"),
                "components": names,
                "url": incident.get("shortlink"),
            }
        )
    found.sort(key=lambda item: item["created_at"] or "", reverse=True)
    state["incidents"] = found
    save_state(state)
    print(f"{len(found)} resolved Actions incidents (newest first):")
    for item in found[:40]:
        window = f"{item['created_at']} -> {item['resolved_at']}"
        print(f"  {item['id']}  {item['impact']:<8} {window}  {item['name'][:70]}")


def window(incident: dict) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(incident["created_at"].replace("Z", "+00:00"))
    end_raw = incident.get("resolved_at") or incident.get("created_at")
    end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
    return start - timedelta(minutes=15), end + timedelta(minutes=30)


def failing_steps(jobs: list[dict]) -> list[dict]:
    out = []
    for job in jobs:
        for step in job.get("steps") or []:
            if step.get("conclusion") not in ("success", "skipped", "neutral", None):
                out.append({"job": job.get("name"), "step": step.get("name"), "conclusion": step.get("conclusion")})
    return out


def looks_environmental(steps: list[dict]) -> bool:
    for step in steps:
        name = str(step.get("step") or "").lower()
        if any(fragment in name for fragment in SETUP_STEPS):
            return True
    return False


def later_success(owner: str, repo: str, sha: str, workflow_id: int, after: str) -> dict | None:
    """A run of the same workflow on the same SHA that succeeded later."""
    payload, _ = api(
        f"/repos/{owner}/{repo}/actions/runs?"
        + urllib.parse.urlencode({"head_sha": sha, "per_page": 50})
    )
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not runs:
        return None
    for run in runs:
        if run.get("workflow_id") != workflow_id:
            continue
        if run.get("conclusion") != "success":
            continue
        if (run.get("created_at") or "") > after:
            return {
                "run_id": run.get("id"),
                "url": run.get("html_url"),
                "created_at": run.get("created_at"),
                "attempt": run.get("run_attempt"),
            }
    return None


def outage_window_runs(repo: str, start: datetime, end: datetime) -> list[dict]:
    payload, _ = api(
        f"/repos/{repo}/actions/runs?"
        + urllib.parse.urlencode(
            {"created": f"{start:%Y-%m-%d}..{end:%Y-%m-%d}", "status": "failure", "per_page": 100}
        )
    )
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    out = []
    for run in runs or []:
        created = run.get("created_at") or ""
        if not created:
            continue
        moment = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if start <= moment <= end:
            out.append(run)
    return out


def environment_text(owner: str, repo: str, jobs: list[dict]) -> tuple[str, list[str], str]:
    """Read the failing job's own log: the runner's words, not a guess about them."""
    failing = [job for job in jobs if job.get("conclusion") not in ("success", None)] or jobs
    if not failing:
        return "", [], ""
    job = failing[0]
    log = job_log(owner, repo, int(job["id"]))
    if log.startswith("__"):
        return "", [], log
    hits = []
    for line in log.splitlines():
        found = INFRA_TEXT.search(line)
        if found:
            hits.append(line.strip()[:180])
    return log, hits[:8], ""


def outage_candidates(state: dict, per_window: int = 1, wanted: int = 3, budget: int = 14) -> None:
    """Failed runs inside an incident window whose own log blames the environment.

    The previous pass required a failing setup step and a later successful run of
    the same workflow on the same sha. That finds almost nothing, because a rerun
    mutates the run it reruns instead of creating a second run. The log is the
    honest signal, so this pass reads the log and looks for the runner's own words.
    """
    incidents = state.get("incidents") or []
    if not incidents:
        print("run `incidents` first")
        return
    ordered = sorted(
        (item for item in incidents if (item.get("impact") or "") in CRITICAL_FIRST),
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )
    out: list[dict] = list(state.get("outage") or [])
    for incident in ordered[:8]:
        if len(out) >= wanted:
            break
        start, end = window(incident)
        found_here = 0
        print(f"window {incident['id']} ({incident.get('impact')}) {start:%Y-%m-%d %H:%M} -> {end:%H:%M}")
        for repo in REPOS:
            if found_here >= per_window or len(out) >= wanted:
                break
            owner, _, name = repo.partition("/")
            runs = outage_window_runs(repo, start, end)[:budget]
            if not runs:
                continue
            print(f"  {repo}: reading {len(runs)} failed runs")
            for run in runs:
                if found_here >= per_window:
                    break
                jobs_payload, _ = api(f"/repos/{repo}/actions/runs/{run['id']}/jobs")
                jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
                if not jobs:
                    continue
                steps = failing_steps(jobs)
                log, hits, note = environment_text(owner, name, jobs)
                if not hits:
                    continue
                success = later_success(
                    owner,
                    name,
                    run.get("head_sha", ""),
                    run.get("workflow_id"),
                    run.get("created_at", ""),
                )
                out.append(
                    {
                        "repo": repo,
                        "run_url": run.get("html_url"),
                        "run_id": run.get("id"),
                        "workflow": run.get("name"),
                        "branch": run.get("head_branch"),
                        "sha": run.get("head_sha"),
                        "created_at": run.get("created_at"),
                        "attempt": run.get("run_attempt"),
                        "failing_steps": steps[:4],
                        "jobs": [
                            {"name": job.get("name"), "conclusion": job.get("conclusion")}
                            for job in jobs
                        ][:8],
                        "log_lines": len(log.splitlines()),
                        "environment_lines": hits,
                        "later_success_run": success,
                        "fetch_note": note,
                        "incident": {
                            "id": incident["id"],
                            "name": incident["name"],
                            "impact": incident["impact"],
                            "created_at": incident["created_at"],
                            "resolved_at": incident["resolved_at"],
                            "url": incident["url"],
                        },
                    }
                )
                found_here += 1
                print(f"  candidate: {repo} #{run['id']} {run.get('name')} :: {hits[0][:100]}")
                state["outage"] = out
                save_state(state)
            if found_here >= per_window:
                break
    state["outage"] = out
    save_state(state)
    print(f"{len(out)} outage candidates")


def unknown_candidates(state: dict, per_status: int = 25) -> None:
    """Cancelled and timed-out runs: a job that never produced an error line."""
    out: list[dict] = []
    for status in ("cancelled", "timed_out"):
        for repo in REPOS:
            payload, _ = api(
                f"/repos/{repo}/actions/runs?"
                + urllib.parse.urlencode({"status": status, "per_page": per_status})
            )
            runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
            if not runs:
                continue
            for run in runs:
                owner, _, name = repo.partition("/")
                jobs_payload, _ = api(f"/repos/{repo}/actions/runs/{run['id']}/jobs")
                jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
                if not jobs:
                    continue
                # A cancelled job with every step skipped has nothing to read.
                conclusions = {job.get("conclusion") for job in jobs}
                if "success" in conclusions:
                    continue
                out.append(
                    {
                        "repo": repo,
                        "run_url": run.get("html_url"),
                        "run_id": run.get("id"),
                        "workflow": run.get("name"),
                        "branch": run.get("head_branch"),
                        "sha": run.get("head_sha"),
                        "created_at": run.get("created_at"),
                        "conclusion": run.get("conclusion"),
                        "attempt": run.get("run_attempt"),
                        "job_conclusions": sorted(str(item) for item in conclusions),
                        "event": run.get("event"),
                    }
                )
                print(f"  candidate: {repo} #{run['id']} {run.get('name')} :: {sorted(str(c) for c in conclusions)}")
                if len(out) >= 40:
                    break
            if len(out) >= 40:
                break
    state["unknown"] = out
    save_state(state)
    print(f"{len(out)} unknown candidates")


def verify(state: dict, run_url: str) -> None:
    """Read one run's failing job log and report what a collector would see."""
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/actions/runs/(\d+)", run_url)
    if not match:
        print("expected https://github.com/owner/repo/actions/runs/<id>")
        return
    owner, repo, run_id = match.groups()
    jobs_payload, _ = api(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
    jobs = jobs_payload.get("jobs") or []
    print(f"{owner}/{repo} run {run_id}: {len(jobs)} jobs")
    for job in jobs:
        steps = failing_steps([job])
        print(
            f"  job {job.get('name')} conclusion={job.get('conclusion')} "
            f"failed_steps={[s['step'] for s in steps]}"
        )
    target = next((job for job in jobs if job.get("conclusion") not in ("success", None)), jobs[0] if jobs else None)
    if target is None:
        return
    text = job_log(owner, repo, int(target["id"]))
    if text.startswith("__"):
        print(f"  log unavailable: {text}")
        return
    lines = text.splitlines()
    hits = [line for line in lines if INFRA_TEXT.search(line)]
    print(f"  log: {len(lines)} lines, {len(hits)} environment-shaped matches")
    for line in hits[:12]:
        print(f"    {line.strip()[:150]}")
    print(f"  tail: {lines[-1].strip()[:150] if lines else '(empty)'}")
    state.setdefault("verified", {})[run_url] = {
        "jobs": [
            {"name": job.get("name"), "conclusion": job.get("conclusion"), "id": job.get("id")}
            for job in jobs
        ],
        "log_lines": len(lines),
        "infra_matches": hits[:20],
        "tail": lines[-3:] if lines else [],
    }
    save_state(state)


COMMANDS = {
    "incidents": lambda state, args: action_incidents(state),
    "outage": lambda state, args: outage_candidates(state),
    "unknown": lambda state, args: unknown_candidates(state),
    "verify": lambda state, args: verify(state, args[0]) if args else print("give a run url"),
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: {Path(__file__).name} {{{'|'.join(COMMANDS)}}}")
        print(f"state file: {STATE}")
        return 2
    state = load_state()
    COMMANDS[argv[0]](state, argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
