"""PyPI dependency collector: manifest diff, resolved versions, release timing."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from redrun.adapters.pypi import PyPIClient
from redrun.collectors import diff as diff_collector
from redrun.collectors import github_runs, logs as logs_collector
from redrun.collectors.base import CollectContext
from redrun.config import ROOT
from redrun.core.hints import package_hint
from redrun.core.models import Evidence
from redrun.core.redact import redact, redact_data

DEP_FILES = re.compile(
    r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|uv\.lock|poetry\.lock|Pipfile\.lock|setup\.cfg)$",
    re.IGNORECASE,
)
BLOCKED_KEYS = {"requires-python", "requires_python", "python", "python_requires"}

Fetcher = Callable[[str], "dict | None"]


@dataclass
class DependencyRecord:
    package: str
    old_version: str | None
    new_version: str | None
    released_at: datetime | None
    released_between_runs: bool
    evidence_id: str


@dataclass
class DepsCollected:
    dependencies: list[DependencyRecord] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


# ------------------------------------------------------------------ parsing


def _clean_version(value: str) -> str:
    return re.sub(r"^[\^~=<>!]+", "", value.strip().strip("\"'")).strip()


def _clean_spec(value: str) -> str:
    """An exact pin becomes a version; a range keeps its operator, which is the fact."""
    spec = value.strip().strip("\"'").strip()
    return spec[2:].strip() if spec.startswith("==") else spec


def _looks_like_version(value: str) -> bool:
    return bool(re.match(r"^[\^~=<>!]*\s*v?\d", value.strip()))


def _parse_package_line(body: str) -> tuple[str, str | None] | None:
    """One added/removed manifest line -> (package, version-ish spec)."""
    body = body.strip()
    if not body or body[0] in "#[":
        return None

    pipfile = re.match(
        r'^"([A-Za-z0-9_.\-]+)"\s*:\s*\{\s*"?version"?\s*:\s*"([^"]+)"', body
    )
    if pipfile:
        return pipfile.group(1).lower(), _clean_spec(pipfile.group(2))

    unquoted = body[1:] if body.startswith('"') else body

    toml = re.match(r'^([A-Za-z0-9_.\-]+)\s*=\s*"([^"]+)"', unquoted)
    if toml:
        name, value = toml.group(1), toml.group(2)
        if name.lower() in BLOCKED_KEYS or not _looks_like_version(value):
            return None
        return name.lower(), _clean_spec(value)

    requirement = re.match(
        r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?\s*([<>=!~^][^;,#\s]*)?", unquoted
    )
    if requirement:
        name, spec = requirement.group(1), requirement.group(2)
        if name.lower() in BLOCKED_KEYS or spec is None:
            return None
        return name.lower(), _clean_spec(spec)
    return None


def parse_manifest_lines(lines: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """Parse a unified diff hunk into {package: (old, new)} for lines that changed."""
    old: dict[str, str | None] = {}
    new: dict[str, str | None] = {}
    for raw in lines:
        if not raw or raw[0] not in "+-":
            continue
        if raw.startswith(("+++", "---")):
            continue
        body = raw[1:].strip().rstrip(",").strip()
        parsed = _parse_package_line(body)
        if parsed is None:
            continue
        name, version = parsed
        (new if raw[0] == "+" else old).setdefault(name, version)

    changed: dict[str, tuple[str | None, str | None]] = {}
    for name in sorted(set(old) | set(new)):
        if old.get(name) != new.get(name):
            changed[name] = (old.get(name), new.get(name))
    return changed


def _parse_release_token(token: str) -> tuple[str, str] | None:
    """`fastapi-0.116.0-py3-none-any.whl` -> (fastapi, 0.116.0)."""
    match = re.match(r"^([A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)*?)-(\d[^-\s]*)", token)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def resolved_versions(log_text: str) -> dict[str, str]:
    """Versions the resolver actually installed, per package. First mention wins."""
    found: dict[str, str] = {}
    for match in re.finditer(r"Downloading ([^\s]+\.(?:whl|tar\.gz))", log_text):
        parsed = _parse_release_token(match.group(1))
        if parsed:
            found.setdefault(parsed[0], parsed[1])
    for match in re.finditer(r"Package `([A-Za-z0-9_.\-]+)==([^`]+)`", log_text):
        found.setdefault(match.group(1).lower(), _clean_version(match.group(2)))
    for match in re.finditer(r"Successfully installed ([^\n]+)", log_text):
        for token in match.group(1).split():
            parsed = _parse_release_token(token)
            if parsed:
                found.setdefault(parsed[0], parsed[1])
    return found


def release_time(payload: dict | None, version: str | None) -> datetime | None:
    if not payload or not version:
        return None
    for entry in (payload.get("releases") or {}).get(version) or []:
        stamp = entry.get("upload_time_iso_8601") or entry.get("upload_time")
        if stamp:
            try:
                return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                return None
    return None


# ------------------------------------------------------------------ fetching


def _sample_fetcher(ctx: CollectContext) -> Fetcher:
    path = ctx.settings.samples_dir / (ctx.sample or "") / "pypi.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return lambda name: data.get(name)


def _fetch_with_cache(name: str, fetcher: Fetcher, cache_dir: Path) -> dict | None:
    cached = cache_dir / f"{name}.json"
    if cached.exists():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except ValueError:
            cached.unlink(missing_ok=True)

    payload = fetcher(name)
    if payload is None:
        return None
    clean = redact_data(payload)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(clean, indent=2, default=str), encoding="utf-8")
    return clean if isinstance(clean, dict) else None


# ------------------------------------------------------------------ collector


def collect(
    ctx: CollectContext,
    runs: github_runs.RunsCollected | None = None,
    changes: diff_collector.DiffCollected | None = None,
    job_logs: logs_collector.LogsCollected | None = None,
    *,
    fetcher: Fetcher | None = None,
    cache_dir: Path | None = None,
) -> DepsCollected:
    """Changed or resolved packages, with the PyPI release time of each new version."""
    if runs is None:
        runs = github_runs.collect(ctx)
    if changes is None:
        changes = diff_collector.collect(ctx, runs)
    if job_logs is None:
        job_logs = logs_collector.collect(ctx, runs)

    patches = {e.metadata.get("filename"): e.excerpt for e in changes.evidence if e.kind == "file"}
    parsed: dict[str, tuple[str | None, str | None]] = {}
    for change in changes.files:
        if DEP_FILES.search(change.filename):
            parsed.update(parse_manifest_lines(patches.get(change.filename, "").splitlines()))

    log_text = "\n".join(e.excerpt for e in job_logs.evidence if not e.metadata.get("tail"))
    resolved = resolved_versions(log_text)
    hinted = {h for h in (package_hint(m.text) for m in job_logs.matches) if h}

    # A manifest change is the strong signal. A package merely named in an error only
    # counts when the log also shows the version it resolved to.
    names = sorted(set(parsed) | (hinted & set(resolved)))

    if fetcher is None:
        fetcher = _sample_fetcher(ctx) if ctx.is_sample else PyPIClient().get
    cache = Path(cache_dir) if cache_dir else ROOT / ".cache" / "pypi"

    out = DepsCollected()
    for name in names:
        old_version, new_spec = parsed.get(name, (None, None))
        new_version = resolved.get(name) or new_spec
        payload = _fetch_with_cache(name, fetcher, cache)
        released = release_time(payload, new_version)
        between = bool(
            released
            and runs.last_green_run is not None
            and runs.last_green_run.created_at <= released <= runs.failed_run.created_at
        )

        evidence_id = f"ev_dep_{name}"
        source_url = (
            f"https://pypi.org/project/{name}/{new_version}/"
            if new_version
            else f"https://pypi.org/project/{name}/"
        )
        out.dependencies.append(
            DependencyRecord(
                package=name,
                old_version=old_version,
                new_version=new_version,
                released_at=released,
                released_between_runs=between,
                evidence_id=evidence_id,
            )
        )
        out.evidence.append(
            Evidence(
                id=evidence_id,
                kind="dependency",
                source_url=source_url,
                title=f"{name} {old_version or '(new)'} -> {new_version or '(unknown)'}",
                excerpt=redact(
                    f"{name}: {old_version or 'unpinned'} -> {new_version or 'unresolved'}; "
                    f"released_at={released.isoformat() if released else 'unknown'}; "
                    f"released_between_runs={between}"
                ),
                timestamp=released or runs.failed_run.created_at,
                metadata={
                    "package": name,
                    "old": old_version,
                    "new": new_version,
                    "released_at": released.isoformat() if released else None,
                    "released_between_runs": between,
                },
            )
        )
    return out
