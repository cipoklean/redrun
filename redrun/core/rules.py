"""Placeholder verdict: a deterministic rules function over collected evidence.

No model is asked for a confidence number. Every point of confidence below is a
named signal with a fixed weight, and `confidence` is the sum of those signals.
"""

import re
from dataclasses import dataclass, field

from redrun.core.logtext import normalise
from redrun.core.models import ConfidenceSignal, Evidence, Link, RunInfo, Verdict
from redrun.core.redact import redact
from redrun.core.taxonomy import Cause

MAX_CONFIDENCE = 0.95
# Below this, the honest answer is `unknown`: the evidence does not separate causes.
MIN_CAUSE_CONFIDENCE = 0.30

MANIFEST = re.compile(
    r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock|Pipfile"
    r"|Pipfile\.lock|package-lock\.json|package\.json|go\.mod|Cargo\.toml|Gemfile)$",
    re.IGNORECASE,
)
SOURCE = re.compile(r"\.(py|pyi|ts|tsx|js|jsx|go|rs|java|rb|kt|swift|php|c|cc|cpp|h)$", re.I)
CONFIG = re.compile(
    r"(^|/)(\.github/workflows/[^/]+|[^/]+\.(ya?ml|ini|cfg|conf)|\.env(\..+)?)$", re.IGNORECASE
)
RESOLUTION_CONFLICT = re.compile(
    r"(?i)(no solution|ResolutionImpossible|conflicting dependencies|version solving failed"
    r"|requires .+ but .+ is installed|could not find a version|no matching distribution)"
)
MODULE_MISSING = re.compile(r"(ModuleNotFoundError|ImportError: cannot import name)")
TRACEBACK = re.compile(
    r"(Traceback \(most recent call last\)"
    r"|^\s*(?:E\s+)?[A-Za-z_][\w.]*(?:Error|Exception):\s)",
    re.MULTILINE,
)
TEST_FAILURE = re.compile(r"(?m)^(?:FAILED|ERROR)\s+\S+|assert .+ == .+")
AUTH_FAILURE = re.compile(
    r"(?i)(\b401\b|\b403\b|unauthorized|forbidden|permission denied|bad credentials"
    r"|invalid token|authentication required)"
)
SECRET_REFERENCE = re.compile(r"(?i)(\bsecret\b|credential|token is empty|secret .+ is empty)")
CONFIG_ERROR = re.compile(
    r"(?i)(invalid config|unknown field|unrecognized|yaml|configuration error|missing required key)"
)
TIMEOUT = re.compile(r"(?i)(timed out|timeout|deadline exceeded)")
INFRA_ERROR = re.compile(
    r"(?i)(no space left on device|disk quota|out of memory|oom-kill|connection reset"
    r"|connection refused|network is unreachable|temporary failure in name resolution"
    r"|could not resolve host|tls handshake)"
)
WORKFLOW_SECRET_USE = re.compile(r"secrets\.[A-Za-z0-9_]+")
SECRET_NAME = re.compile(r"(?i)(?:secret|token)[\s:=]+([A-Z][A-Z0-9_]{2,})")
ERROR_TYPE = re.compile(r"\b([A-Za-z_][\w.]*(?:Error|Exception))\b")


@dataclass
class Signal:
    name: str
    weight: float
    value: float
    cited: list[str] = field(default_factory=list)


@dataclass
class Case:
    cause: Cause
    signals: list[Signal]
    quote: str = ""

    @property
    def score(self) -> float:
        return sum(s.weight * s.value for s in self.signals)


def _line_at(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start : end if end != -1 else len(text)].strip()


def _search(
    evidence: list[Evidence],
    regex: re.Pattern[str],
    *,
    kinds: tuple[str, ...] = ("log",),
    include_tails: bool = False,
    field_name: str = "excerpt",
) -> tuple[list[str], str]:
    """Return (cited ids, first matching line). Ids are what the verdict cites."""
    ids: list[str] = []
    quote = ""
    for item in evidence:
        if item.kind not in kinds:
            continue
        if item.metadata.get("tail") and not include_tails:
            continue
        haystack = item.excerpt if field_name == "excerpt" else str(item.metadata.get(field_name, ""))
        if item.kind == "log":
            # Excerpts keep their ISO timestamps for fidelity; matching must not.
            haystack = normalise(haystack)
        match = regex.search(haystack)
        if match:
            ids.append(item.id)
            if not quote:
                quote = _line_at(haystack, match.start())
    return ids, quote


def _signal(
    name: str, weight: float, ids: list[str], *, hit: bool | None = None
) -> Signal:
    value = 1.0 if (hit if hit is not None else bool(ids)) else 0.0
    return Signal(name=name, weight=weight, value=value, cited=ids if value else [])


def _link_signal(links: list[Link], link_type: str, weight: float) -> Signal:
    """A cross-source join from overlap.sql. Its score_hint scales the contribution."""
    matching = [link for link in links if link.link_type == link_type]
    cited: list[str] = []
    for link in matching:
        for candidate in (link.log_evidence_id, link.change_evidence_id):
            if candidate and candidate not in cited:
                cited.append(candidate)
    value = min(1.0, max((link.score_hint for link in matching), default=0.0))
    return Signal(f"link_{link_type}", weight, value, cited if value else [])


def _dependency_case(
    evidence: list[Evidence], failed: RunInfo, green: RunInfo | None, links: list[Link]
) -> Case:
    conflict_ids, conflict_line = _search(evidence, RESOLUTION_CONFLICT)
    module_ids, module_line = _search(evidence, MODULE_MISSING)
    manifest_ids, _ = _search(evidence, MANIFEST, kinds=("file",), field_name="filename")
    signals = [
        _link_signal(links, "dependency_match", 0.30),
        _signal("log_resolution_conflict", 0.30, conflict_ids),
        _signal("log_module_not_found", 0.15, module_ids),
        _signal("diff_dependency_manifest_changed", 0.15, manifest_ids),
    ]
    return Case(Cause.DEPENDENCY_BUMP, signals, conflict_line or module_line)


def _bad_commit_case(
    evidence: list[Evidence], failed: RunInfo, green: RunInfo | None, links: list[Link]
) -> Case:
    error_ids, error_line = _search(evidence, TRACEBACK)
    fail_ids, fail_line = _search(evidence, TEST_FAILURE)
    source_ids, _ = _search(evidence, SOURCE, kinds=("file",), field_name="filename")
    signals = [
        _link_signal(links, "file_match", 0.30),
        _signal("log_python_error", 0.30, error_ids),
        _signal("diff_source_changed", 0.20, source_ids),
        _signal("log_test_failure", 0.10, fail_ids),
    ]
    return Case(Cause.BAD_COMMIT, signals, error_line or fail_line)


def _secret_case(
    evidence: list[Evidence], failed: RunInfo, green: RunInfo | None, links: list[Link]
) -> Case:
    auth_ids, auth_line = _search(evidence, AUTH_FAILURE)
    secret_ids, secret_line = _search(evidence, SECRET_REFERENCE)
    workflow_ids, _ = _search(evidence, WORKFLOW_SECRET_USE, kinds=("file",))
    signals = [
        _link_signal(links, "secret_or_config_match", 0.30),
        _signal("log_auth_failure", 0.30, auth_ids),
        _signal("log_secret_reference", 0.15, secret_ids),
        _signal("diff_workflow_secret_change", 0.10, workflow_ids),
    ]
    return Case(Cause.EXPIRED_OR_MISSING_SECRET, signals, auth_line or secret_line)


def _config_case(
    evidence: list[Evidence], failed: RunInfo, green: RunInfo | None, links: list[Link]
) -> Case:
    config_ids, _ = _search(evidence, CONFIG, kinds=("file",), field_name="filename")
    manifest_ids, _ = _search(evidence, MANIFEST, kinds=("file",), field_name="filename")
    manifest_set = set(manifest_ids)
    config_only = [i for i in config_ids if i not in manifest_set]
    error_ids, error_line = _search(evidence, CONFIG_ERROR)
    signals = [
        _link_signal(links, "config_match", 0.30),
        _link_signal(links, "secret_or_config_match", 0.15),
        _signal("diff_config_file_changed", 0.25, config_only),
        _signal("log_config_error", 0.20, error_ids),
    ]
    return Case(Cause.CONFIG_CHANGE, signals, error_line)


def _flaky_case(
    evidence: list[Evidence], failed: RunInfo, green: RunInfo | None, links: list[Link]
) -> Case:
    fail_ids, fail_line = _search(evidence, TEST_FAILURE)
    timeout_ids, _ = _search(evidence, TIMEOUT)
    same_commit = bool(green and green.head_sha == failed.head_sha)
    file_ids, _ = _search(evidence, re.compile(r"."), kinds=("file",), field_name="filename")
    # Absence of a diff is only evidence if a diff was actually compared and came back
    # with commits but no files. With no evidence at all we know nothing, and must not
    # score our own ignorance as a signal.
    compared_a_range = bool(green) and any(e.kind == "commit" for e in evidence)
    no_code_change = compared_a_range and not file_ids
    signals = [
        _signal("log_test_failure", 0.35, fail_ids),
        _signal("same_commit_previously_green", 0.35, [], hit=same_commit),
        _signal("log_timeout", 0.20, timeout_ids),
        _signal("no_code_change_in_range", 0.15, [], hit=no_code_change),
    ]
    return Case(Cause.FLAKY_TEST, signals, fail_line)


def _infra_case(
    evidence: list[Evidence], failed: RunInfo, green: RunInfo | None, links: list[Link]
) -> Case:
    infra_ids, infra_line = _search(evidence, INFRA_ERROR)
    timeout_ids, _ = _search(evidence, TIMEOUT)
    changed_runner = bool(
        green and green.runner_image and failed.runner_image
        and green.runner_image != failed.runner_image
    )
    signals = [
        _signal("log_infra_error", 0.50, infra_ids),
        _signal("log_timeout", 0.25, timeout_ids),
        _signal("runner_image_changed", 0.20, [], hit=changed_runner),
    ]
    return Case(Cause.INFRA_OUTAGE, signals, infra_line)


CASES = (_dependency_case, _bad_commit_case, _secret_case, _config_case, _flaky_case, _infra_case)


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _next_step(cause: Cause, evidence: list[Evidence], failed: RunInfo, green: RunInfo | None) -> str:
    manifests = [e.metadata.get("filename") for e in evidence if e.kind == "file"
                 and MANIFEST.search(str(e.metadata.get("filename", "")))]
    sources = [e.metadata.get("filename") for e in evidence if e.kind == "file"
               and SOURCE.search(str(e.metadata.get("filename", "")))]
    configs = [e.metadata.get("filename") for e in evidence if e.kind == "file"
               and CONFIG.search(str(e.metadata.get("filename", "")))]
    secret_hits = [
        m.group(1)
        for e in evidence
        if e.kind == "log" and not e.metadata.get("tail")
        for m in SECRET_NAME.finditer(e.excerpt)
    ]
    error_hits = [
        m.group(1)
        for e in evidence
        if e.kind == "log" and not e.metadata.get("tail")
        for m in ERROR_TYPE.finditer(e.excerpt)
    ]

    if cause is Cause.DEPENDENCY_BUMP:
        target = manifests[0] if manifests else "the dependency manifest"
        return f"Re-pin the bump in {target} so the resolver finds a consistent set, then re-run."
    if cause is Cause.BAD_COMMIT:
        target = sources[0] if sources else "the changed source file"
        kind = error_hits[0] if error_hits else "the error"
        baseline = _short(green.head_sha) if green else "the last green commit"
        return f"Open {target} at {_short(failed.head_sha)} and fix {kind}; diff it against {baseline}."
    if cause is Cause.EXPIRED_OR_MISSING_SECRET:
        name = f" {secret_hits[0]}" if secret_hits else ""
        return f"Rotate the credential and update repository secret{name}, then re-run the failed job."
    if cause is Cause.CONFIG_CHANGE:
        target = configs[0] if configs else "the changed config file"
        return f"Revert or correct {target}, then re-run."
    if cause is Cause.FLAKY_TEST:
        return "Re-run the failed job once; if it passes, quarantine the test before it costs another cycle."
    if cause is Cause.INFRA_OUTAGE:
        return "Re-run the job now; if it fails at the same step, check runner disk, memory and network."
    return "Collect more evidence: fetch the full job log or re-run with debug logging enabled."


def _root_cause(cause: Cause, quote: str, failed: RunInfo, green: RunInfo | None) -> str:
    base = _short(green.head_sha) if green else "no green baseline"
    head = _short(failed.head_sha)
    detail = redact(quote)[:240] if quote else "no single line names the cause"
    if cause is Cause.DEPENDENCY_BUMP:
        return f"The dependency graph cannot be resolved between {base} and {head}: {detail}"
    if cause is Cause.BAD_COMMIT:
        return f"The code introduced in {head} fails at runtime, not the environment: {detail}"
    if cause is Cause.EXPIRED_OR_MISSING_SECRET:
        return f"The run cannot authenticate because a credential is missing or expired: {detail}"
    if cause is Cause.CONFIG_CHANGE:
        return f"A configuration change is rejected by the run: {detail}"
    if cause is Cause.FLAKY_TEST:
        return f"The same commit passed before and failed now, with no relevant code change: {detail}"
    if cause is Cause.INFRA_OUTAGE:
        return f"The runner environment failed before the code could run: {detail}"
    return "No cause reached the evidence threshold; the log and the diff do not agree."


def rule_verdict(
    evidence: list[Evidence],
    failed: RunInfo,
    green: RunInfo | None,
    links: list[Link] | None = None,
) -> Verdict:
    """Score every cause from its signals and return the best-supported one."""
    link_list = list(links or [])
    cases = [build(evidence, failed, green, link_list) for build in CASES]
    best = max(cases, key=lambda c: c.score)

    if best.score < MIN_CAUSE_CONFIDENCE:
        return Verdict(
            cause=Cause.UNKNOWN,
            root_cause=_root_cause(Cause.UNKNOWN, "", failed, green),
            confidence=min(round(best.score, 2), MIN_CAUSE_CONFIDENCE - 0.01),
            confidence_breakdown=[
                ConfidenceSignal(signal=s.name, weight=s.weight, value=s.value)
                for s in best.signals
                if s.value
            ],
            next_step=_next_step(Cause.UNKNOWN, evidence, failed, green),
            cited_evidence_ids=[],
        )

    cited: list[str] = []
    for signal in best.signals:
        for evidence_id in signal.cited:
            if evidence_id not in cited:
                cited.append(evidence_id)

    return Verdict(
        cause=best.cause,
        root_cause=_root_cause(best.cause, best.quote, failed, green),
        confidence=min(MAX_CONFIDENCE, round(best.score, 2)),
        confidence_breakdown=[
            ConfidenceSignal(signal=s.name, weight=s.weight, value=s.value) for s in best.signals
        ],
        next_step=_next_step(best.cause, evidence, failed, green),
        cited_evidence_ids=cited,
    )
