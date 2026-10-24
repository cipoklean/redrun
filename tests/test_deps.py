"""Dependency collector: manifest parsing, resolved versions from logs, PyPI timing."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from redrun.collectors import deps as deps_collector
from redrun.collectors.base import CollectContext
from redrun.config import Settings
from redrun.core.models import Evidence

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
PYPI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pypi"


def settings(tmp_path: Path) -> Settings:
    return Settings(sample_mode=True, samples_dir=SAMPLES, logs_dir=tmp_path / "logs")


def ctx(tmp_path: Path, sample: str = "dependency_bump") -> CollectContext:
    return CollectContext(settings=settings(tmp_path), sample=sample)


def fixture_fetcher(name: str) -> dict | None:
    path = PYPI_FIXTURES / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


# ---------------------------------------------------------------- parsing


def test_pyproject_patch_yields_old_and_new_versions(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    by_name = {d.package: d for d in got.dependencies}
    assert by_name["fastapi"].old_version == ">=0.110"
    assert by_name["httpx"].old_version == ">=0.27"
    assert by_name["fastapi"].new_version == "0.116.0"
    assert by_name["httpx"].new_version == "0.28.1"


def test_resolved_versions_come_from_the_logs_not_the_spec(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    fastapi = next(d for d in got.dependencies if d.package == "fastapi")
    assert fastapi.new_version == "0.116.0", "the spec says >=0.116, the log resolved 0.116.0"


def test_requirements_txt_is_parsed(tmp_path: Path) -> None:
    parsed = deps_collector.parse_manifest_lines(
        ["-requests==2.31.0", "+requests==2.32.3", "+urllib3==2.2.3"]
    )
    assert parsed["requests"] == ("2.31.0", "2.32.3")
    assert parsed["urllib3"] == (None, "2.2.3")


def test_lockfile_style_lines_are_parsed() -> None:
    parsed = deps_collector.parse_manifest_lines(
        ['-name = "1.8.0"', '+name = "1.9.0"', '+    "flask": {"version": "==3.0.1"},']
    )
    assert parsed["name"] == ("1.8.0", "1.9.0")
    assert parsed["flask"] == (None, "3.0.1")


def test_non_dependency_lines_are_ignored() -> None:
    parsed = deps_collector.parse_manifest_lines(
        ["+[project]", '+requires-python = ">=3.11"', "+# a comment", '+name = "redrun"']
    )
    assert parsed == {}


# ---------------------------------------------------------------- PyPI timing


def test_release_between_runs_is_true_only_inside_the_window(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    by_name = {d.package: d for d in got.dependencies}
    assert by_name["fastapi"].released_between_runs is True
    assert by_name["httpx"].released_between_runs is False


def test_release_timestamp_is_recorded_as_utc(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    fastapi = next(d for d in got.dependencies if d.package == "fastapi")
    assert fastapi.released_at == datetime(2026, 10, 23, 7, 12, tzinfo=timezone.utc)


def test_missing_pypi_data_degrades_instead_of_failing(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=lambda name: None, cache_dir=tmp_path / "cache")
    assert got.dependencies, "manifests still yield dependencies without PyPI"
    assert all(d.released_between_runs is False for d in got.dependencies)
    assert all(d.released_at is None for d in got.dependencies)


# ---------------------------------------------------------------- evidence


def test_dependency_evidence_matches_the_contract(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    fastapi = next(e for e in got.evidence if e.id == "ev_dep_fastapi")
    assert fastapi.kind == "dependency"
    assert fastapi.source_url == "https://pypi.org/project/fastapi/0.116.0/"
    assert fastapi.metadata["old"] == ">=0.110"
    assert fastapi.metadata["new"] == "0.116.0"
    assert fastapi.metadata["released_between_runs"] is True
    assert fastapi.metadata["released_at"].startswith("2026-10-23T07:12:00")
    assert fastapi.metadata["package"] == "fastapi"


def test_evidence_ids_are_stable_and_unique(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    ids = [e.id for e in got.evidence]
    assert ids == sorted(set(ids))
    assert ids == ["ev_dep_fastapi", "ev_dep_httpx"]


# ---------------------------------------------------------------- cache


def test_pypi_responses_are_cached_and_redacted(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    calls: list[str] = []

    def fetcher(name: str) -> dict:
        calls.append(name)
        return {
            "info": {"name": name},
            "releases": {"0.116.0": [{"upload_time_iso_8601": "2026-10-23T07:12:00Z"}]},
            "debug_note": "leaked ghp_FAKEfakeFAKEfake1234FAKEfake1234",
        }

    deps_collector.collect(ctx(tmp_path), fetcher=fetcher, cache_dir=cache)
    assert calls, "the fetcher should have been called the first time"
    cached = cache / "fastapi.json"
    assert cached.exists()
    assert "ghp_FAKEfake" not in cached.read_text()
    assert "[REDACTED]" in cached.read_text()

    before = len(calls)
    deps_collector.collect(ctx(tmp_path), fetcher=fetcher, cache_dir=cache)
    assert len(calls) == before, "second run must read the cache, not the network"


def test_sample_mode_reads_recorded_pypi_data_without_a_fetcher(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=None, cache_dir=tmp_path / "cache")
    by_name = {d.package: d for d in got.dependencies}
    assert by_name["fastapi"].released_between_runs is True


def test_no_dependency_files_means_no_dependencies(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path, "bad_commit"), fetcher=None, cache_dir=tmp_path / "cache")
    assert got.dependencies == []
    assert got.evidence == []


def test_evidence_excerpt_is_redacted(tmp_path: Path) -> None:
    got = deps_collector.collect(ctx(tmp_path), fetcher=fixture_fetcher, cache_dir=tmp_path / "cache")
    assert all(isinstance(e, Evidence) for e in got.evidence)
    assert all("ghp_" not in e.excerpt for e in got.evidence)
