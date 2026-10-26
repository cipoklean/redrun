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


def token_settings(**overrides) -> Settings:
    base = {
        "sample_mode": False,
        "zetaris_url": "http://localhost:8888/api/v1.0",
        "zetaris_token": "tok-123",
    }
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


def _recorder(responses: list[httpx.Response] | None = None):
    """A transport that records requests and replays a queued response per call."""
    seen: list[httpx.Request] = []
    queue = list(responses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if queue:
            return queue.pop(0)
        return httpx.Response(200, json={"data": []})

    return seen, httpx.MockTransport(handler)


def _statements(seen: list[httpx.Request]) -> list[str]:
    return [json.loads(request.content)["sql"] for request in seen]


def test_register_sources_is_in_place_and_creates_one_database_per_trace() -> None:
    seen, transport = _recorder()
    engine = ZetarisEngine(
        token_settings(zetaris_data_root="/mnt/shared/redrun"), transport=transport
    )

    method = engine.register_sources("a1b2c3d4e5f6", TABLE_PATHS)

    assert method == "in_place"
    assert engine.method == "in_place"
    assert engine.catalog == "redrun_a1b2c3d4e5f6"
    statements = _statements(seen)
    assert statements[0] == (
        "CREATE LIGHTNING DATABASE redrun_a1b2c3d4e5f6 DESCRIBE BY 'RedRun evidence'"
    )
    assert statements[1] == (
        "CREATE LIGHTNING FILESTORE TABLE runs FROM redrun_a1b2c3d4e5f6 "
        'FORMAT PARQUET OPTIONS (path "/mnt/shared/redrun/a1b2c3d4e5f6/runs.parquet")'
    )
    assert len(statements) == 5, "one database plus four tables"
    for table in ("runs", "changes", "log_errors", "dependencies"):
        assert any(f"FILESTORE TABLE {table} FROM redrun_a1b2c3d4e5f6" in s for s in statements), table


def test_the_database_name_is_sanitised_to_lowercase_underscores() -> None:
    engine = ZetarisEngine(token_settings(), transport=_recorder()[1])
    engine.register_sources("A1- b2/C3", TABLE_PATHS)
    assert engine.catalog == "redrun_a1__b2_c3"


def test_only_the_tables_that_exist_are_registered() -> None:
    seen, transport = _recorder()
    engine = ZetarisEngine(token_settings(), transport=transport)
    engine.register_sources("t1", {"runs": Path("/tmp/runs.parquet")})
    statements = _statements(seen)
    assert len(statements) == 2
    assert "FILESTORE TABLE runs" in statements[1]


def test_an_already_existing_database_and_table_are_tolerated() -> None:
    seen, transport = _recorder(
        [httpx.Response(400, text="Database already exists: redrun_t1")]
    )
    engine = ZetarisEngine(token_settings(), transport=transport)

    method = engine.register_sources("t1", TABLE_PATHS)

    assert method == "in_place"
    statements = _statements(seen)
    assert len(statements) == 5, "a tolerated error must not stop the run"
    assert engine.catalog == "redrun_t1"


def test_an_unrelated_ddl_error_still_fails() -> None:
    _seen, transport = _recorder(
        [httpx.Response(400, text="LightningDdlParseException: no viable alternative")]
    )
    engine = ZetarisEngine(token_settings(), transport=transport)
    with pytest.raises(QueryFailed):
        engine.register_sources("t1", TABLE_PATHS)


def test_queries_qualify_to_the_per_trace_database() -> None:
    seen, transport = _recorder()
    engine = ZetarisEngine(token_settings(), transport=transport)
    engine.register_sources("t1", TABLE_PATHS)
    seen.clear()

    engine.query("SELECT * FROM runs")

    assert _statements(seen) == ["SELECT * FROM redrun_t1.runs"]


def test_login_org_id_reaches_the_proxy_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == PATH_UI_LOGIN:
            return httpx.Response(
                200, json={"idToken": "aaa.bbb.ccc", "refreshToken": "r", "orgId": "org-42"}
            )
        assert request.headers["X-Org-ID"] == "org-42", request.headers
        return httpx.Response(200, json={"data": [["1"]]})

    engine = ZetarisEngine(ui_settings(), transport=httpx.MockTransport(handler))
    result = engine.query("SELECT 1")

    assert engine._ui_org == "org-42"
    assert result.row_count == 1


def test_the_configured_org_is_used_when_login_omits_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == PATH_UI_LOGIN:
            return httpx.Response(200, json={"idToken": "aaa.bbb.ccc"})
        assert request.headers["X-Org-ID"] == "7"
        return httpx.Response(200, json={"data": []})

    engine = ZetarisEngine(
        ui_settings().__class__(
            sample_mode=False,
            zetaris_url="http://localhost:3000",
            zetaris_user="david",
            zetaris_password="hunter2",
            zetaris_org="7",
        ),
        transport=httpx.MockTransport(handler),
    )
    engine.query("SELECT 1")
    assert engine._ui_org is None, "no orgId in the login body must not invent one"


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


def test_run_overlap_registers_in_place_then_queries_the_trace_database(tmp_path: Path) -> None:
    """The whole path, against a stub that speaks the proxy shape."""
    from redrun.pipeline import prepare, run_overlap

    settings = Settings(
        sample_mode=True,
        samples_dir=Path(__file__).resolve().parents[1] / "samples",
        logs_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
        zetaris_url="http://localhost:3000",
        zetaris_user="u",
        zetaris_password="p",
        zetaris_data_root="/mnt/redrun",
    )
    prepared = prepare(None, "dependency_bump", settings)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == PATH_UI_LOGIN:
            return httpx.Response(200, json={"idToken": "aaa.bbb.ccc", "orgId": "9"})
        seen.append(request)
        sql = json.loads(request.content)["sql"]
        if sql.lower().startswith("create lightning"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            200,
            json={
                "data": [
                    ["dependency_match", "ev_log_001", "ev_dep_fastapi", 0.85, "fastapi 0.116.0"]
                ]
            },
        )

    engine = ZetarisEngine(settings, transport=httpx.MockTransport(handler))
    links, label, skipped = run_overlap(prepared, settings, "zetaris", engine=engine)

    database = f"redrun_{prepared.trace_id}"
    assert label == "zetaris"
    assert engine.method == "in_place"
    assert engine.catalog == database
    assert [link.link_type for link in links] == ["dependency_match"]
    assert skipped == 0

    statements = [json.loads(request.content)["sql"] for request in seen]
    assert statements[0] == (
        f"CREATE LIGHTNING DATABASE {database} DESCRIBE BY 'RedRun evidence'"
    )
    assert any(
        statement.startswith(f"CREATE LIGHTNING FILESTORE TABLE runs FROM {database}")
        for statement in statements
    )
    overlap = statements[-1]
    for table in ("runs", "changes", "log_errors", "dependencies"):
        assert f"{database}.{table}" in overlap, table

    events = [
        json.loads(line) for line in prepared.tracer.path.read_text().splitlines() if line.strip()
    ]
    result = [event for event in events if event["agent"] == "zetaris" and event["type"] == "result"]
    assert result[-1]["data"]["method"] == "in_place"
    assert result[-1]["data"]["database"] == database
