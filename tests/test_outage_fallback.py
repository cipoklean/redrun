"""A refuted proposal during a GitHub incident falls back to infra_outage, but only
when the failed run carries the outage's fingerprint."""

from pathlib import Path

import pytest

from redrun.collectors.logs import PATTERNS
from redrun.config import Settings
from redrun.core.taxonomy import Cause
from redrun.pipeline import investigate

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        sample_mode=True,
        samples_dir=SAMPLES,
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
    )


@pytest.mark.parametrize(
    "sample",
    ["eval_infra_outage_django_connection_reset", "eval_infra_outage_pandas_action_download"],
)
def test_incident_plus_network_error_is_an_outage(tmp_path: Path, sample: str) -> None:
    result = investigate(None, sample=sample, settings=settings(tmp_path))
    verdict = result.verdict
    assert verdict.cause is Cause.INFRA_OUTAGE
    assert any(item.startswith("ev_outage_") for item in verdict.cited_evidence_ids)
    ids = {item.id for item in result.evidence}
    assert set(verdict.cited_evidence_ids) <= ids
    outage = next(c for c in result.skeptic.checks if c.check == "outage_overlap")
    assert outage.status == "pass"


def test_a_cancelled_run_is_never_called_an_outage(tmp_path: Path) -> None:
    result = investigate(None, sample="eval_unknown_django_cancelled", settings=settings(tmp_path))
    assert result.verdict.cause is Cause.UNKNOWN


@pytest.mark.parametrize(
    "line",
    [
        "##[error]Failed to download archive 'https://codeload.github.com/x/y/zip/abc' after 3 attempts.",
        "##[error]Response status code does not indicate success: 429 (Too Many Requests).",
        "  HTTP Error 502: Bad Gateway",
        "ConnectionResetError(104, 'Connection reset by peer')",
    ],
)
def test_platform_errors_are_classed_as_infra(line: str) -> None:
    infra = dict(PATTERNS)["infra_env"]
    assert infra.search(line)
