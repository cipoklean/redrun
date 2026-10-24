"""Zetaris adapter: confirmed endpoints only, honest NotConfigured everywhere else."""

import json
from pathlib import Path

import httpx
import pytest

from redrun.adapters.base import NotConfigured, QueryFailed
from redrun.adapters.zetaris import (
    PATH_DATASOURCES,
    PATH_RUN,
    PATH_UI_DATABASES,
    PATH_UI_LOGIN,
    PATH_UI_RUN,
    ZetarisEngine,
)
from redrun.config import Settings

TABLE_PATHS = {name: Path(f"/tmp/t/{name}.parquet") for name in
               ("runs", "changes", "log_errors", "dependencies")}


def _real_tables(tmp_path: Path) -> dict[str, Path]:
    """Real Parquet files, so the load fallback exercises the actual read path."""
    import duckdb

    from redrun.core.tables import SCHEMAS, TABLE_NAMES

    connection = duckdb.connect()
    paths: dict[str, Path] = {}
    try:
        for name in TABLE_NAMES:
            columns = SCHEMAS[name]
            connection.execute(
                f"CREATE TABLE {name} ({', '.join(f'{col} {kind}' for col, kind in columns)})"
            )
            if name == "runs":
                connection.execute(
                    "INSERT INTO runs VALUES "
                    "(1, 'failed', 'abc', '2026-01-01T00:00:00+00:00', 'failure', 'ubuntu', NULL)"
                )
            path = tmp_path / f"{name}.parquet"
            connection.execute(f"COPY {name} TO '{path}' (FORMAT PARQUET)")
            paths[name] = path
    finally:
        connection.close()
    return paths


def token_settings(**overrides) -> Settings:
    base = dict(
        sample_mode=False,
        zetaris_url="http://localhost:8888/api/v1.0",
        zetaris_token="tok-123",
    )
    base.update(overrides)
    return Settings(**base)


def ui_settings() -> Settings:
    return Settings(
        sample_mode=False,
        zetaris_url="http://localhost:3000",
        zetaris_user="david",
        zetaris_password="hunter2",
    )


def test_endpoint_constants_are_the_confirmed_ones() -> None:
    # Source: github.com/zetaris/getting-started, scripts/zetaris_sql.py (RestChannel)
    assert PATH_DATASOURCES == "/datasource/datasources"
    assert PATH_RUN == "/sql-editor/sqls/run"
    # Source: scripts/query_zetaris.py, scripts/check_zetaris.py, scripts/zetaris_api.py
    assert PATH_UI_RUN == "/api/proxy/sql-editor/sqls/run-query"
    assert PATH_UI_DATABASES == "/api/proxy/lightning-database/databases"
    assert PATH_UI_LOGIN == "/api/auth/login"


def test_unconfigured_engine_refuses_instead_of_faking() -> None:
    engine = ZetarisEngine(Settings(sample_mode=False))
    assert engine.configured is False
    assert engine.status() == "offline"
    with pytest.raises(NotConfigured):
        engine.query("SELECT 1")
    with pytest.raises(NotConfigured):
        engine.register_sources("t", TABLE_PATHS, method="in_place")


def test_status_online_uses_the_datasources_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    engine = ZetarisEngine(token_settings(), transport=httpx.MockTransport(handler))
    assert engine.status() == "online"
    assert seen[0].url.path == "/api/v1.0/datasource/datasources"
    assert seen[0].headers["authorization"] == "Bearer tok-123"
    assert seen[0].headers["x-org-id"] == "1"


@pytest.mark.parametrize("code,expected", [(401, "unauthorized"), (403, "unauthorized"), (500, "offline")])
def test_status_maps_codes(code: int, expected: str) -> None:
    engine = ZetarisEngine(
        token_settings(),
        transport=httpx.MockTransport(lambda r: httpx.Response(code, json={})),
    )
    assert engine.status() == expected


def test_status_offline_when_unreachable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    engine = ZetarisEngine(token_settings(), transport=httpx.MockTransport(refuse))
    assert engine.status() == "offline"


def test_query_posts_the_confirmed_body_and_parses_rows() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [["file_match", "ev_log_001", "ev_file_001", 0.8, "x"]]})

    engine = ZetarisEngine(token_settings(), transport=httpx.MockTransport(handler))
    result = engine.query("SELECT 1")

    assert result.row_count == 1
    assert result.rows[0]["link_type"] == "file_match"
    assert result.rows[0]["score_hint"] == 0.8
    body = json.loads(seen[0].content)
    assert body["sql"] == "SELECT 1"
    assert body["source"] == "SqlEditor"
    assert body["limit"] > 0
    assert "queryId" in body
    assert result.sql_sha256 and result.latency_ms >= 0


def test_credentials_are_never_returned_in_the_result() -> None:
    engine = ZetarisEngine(
        token_settings(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
    )
    result = engine.query("SELECT 1")
    assert "tok-123" not in json.dumps(result.rows)


def test_a_failed_statement_is_a_query_error() -> None:
    engine = ZetarisEngine(
        token_settings(),
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text="bad sql")),
    )
    with pytest.raises(QueryFailed) as excinfo:
        engine.query("SELECT nope")
    assert "400" in str(excinfo.value)


def test_user_password_logs_in_then_uses_the_ui_proxy() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"idToken": "ui-token"})
        return httpx.Response(200, json={"data": []})

    engine = ZetarisEngine(ui_settings(), transport=httpx.MockTransport(handler))
    engine.query("SELECT 1")

    assert [r.url.path for r in seen] == ["/api/auth/login", "/api/proxy/sql-editor/sqls/run-query"]
    assert seen[1].headers["authorization"] == "Bearer ui-token"
    assert json.loads(seen[0].content) == {"username": "david", "password": "hunter2"}


def test_missing_ui_token_is_reported_not_guessed() -> None:
    engine = ZetarisEngine(
        ui_settings(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"unexpected": True})),
    )
    with pytest.raises(NotConfigured):
        engine.query("SELECT 1")


def test_register_sources_prefers_in_place_and_raises_when_unconfirmed() -> None:
    engine = ZetarisEngine(token_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(NotConfigured) as excinfo:
        engine.register_sources("t1", TABLE_PATHS, method="in_place")
    assert "datasource" in str(excinfo.value).lower()


def test_register_sources_falls_back_to_loading_rows_into_tables(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    engine = ZetarisEngine(token_settings(), transport=httpx.MockTransport(handler))
    method = engine.register_sources("t1", _real_tables(tmp_path))

    assert method == "loaded_table"
    statements = [json.loads(r.content)["sql"] for r in seen]
    for table in ("runs", "changes", "log_errors", "dependencies"):
        assert any(f"CREATE TABLE IF NOT EXISTS {table}" in s for s in statements), table
    assert any(s.startswith("INSERT INTO runs VALUES") for s in statements)


def test_catalog_qualifies_known_table_names_only() -> None:
    engine = ZetarisEngine(token_settings(zetaris_catalog="redrun"))
    qualified = engine._qualify("SELECT * FROM runs JOIN log_errors ON 1=1 WHERE x = 'runs'")
    assert "redrun.runs" in qualified
    assert "redrun.log_errors" in qualified
    assert "'runs'" in qualified, "string literals must not be rewritten"


def test_catalog_is_off_by_default() -> None:
    engine = ZetarisEngine(token_settings())
    assert engine._qualify("SELECT * FROM runs") == "SELECT * FROM runs"


def test_parse_rows_prefers_column_metadata_when_present() -> None:
    engine = ZetarisEngine(token_settings())
    payload = {"columns": [{"name": "link_type"}, {"name": "detail"}], "data": [["file_match", "x"]]}
    assert engine._rows(payload) == [{"link_type": "file_match", "detail": "x"}]


def test_scalar_rows_are_not_pretended_to_be_link_rows() -> None:
    engine = ZetarisEngine(token_settings())
    assert engine._rows({"data": [["1"]]}) == [{"col_1": "1"}]


def test_unreachable_engine_raises_query_failed_not_httpx() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    engine = ZetarisEngine(token_settings(), transport=httpx.MockTransport(refuse))
    with pytest.raises(QueryFailed):
        engine.query("SELECT 1")
