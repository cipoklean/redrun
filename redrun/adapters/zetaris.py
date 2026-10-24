"""Zetaris adapter. Every endpoint below is confirmed against first-party code.

Do not add a path here without a source. An unconfirmed capability must raise
NotConfigured, so a demo can never show a green Zetaris step that never ran.
"""

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import duckdb
import httpx

from redrun.adapters.base import NotConfigured, QueryFailed, QueryResult
from redrun.config import Settings
from redrun.core.tables import SCHEMAS

# --- Confirmed endpoints -----------------------------------------------------
# Source: github.com/zetaris/getting-started/blob/main/scripts/zetaris_sql.py
#   DEFAULT_REST_URL = "http://localhost:8888/api/v1.0"
#   RestChannel.datasources() -> GET  {base}/datasource/datasources
#   RestChannel.run()         -> POST {base}/sql-editor/sqls/run
#       body    {"queryId": <uuid>, "sql": <sql>, "source": "SqlEditor", "limit": <n>}
#       headers Authorization: Bearer <key>, X-Org-ID: <org>, X-Request-ID: <uuid>
#       returns {"data": [[...], ...]}
REST_BASE_DEFAULT = "http://localhost:8888/api/v1.0"
PATH_DATASOURCES = "/datasource/datasources"
PATH_RUN = "/sql-editor/sqls/run"

# Source: github.com/zetaris/getting-started/blob/main/scripts/zetaris_api.py
#   POST {ui}/api/auth/login {"username","password"} -> idToken | token | accessToken
# Source: .../scripts/query_zetaris.py  -> POST /api/proxy/sql-editor/sqls/run-query
# Source: .../scripts/check_zetaris.py  -> GET  /api/proxy/lightning-database/databases
PATH_UI_LOGIN = "/api/auth/login"
PATH_UI_RUN = "/api/proxy/sql-editor/sqls/run-query"
PATH_UI_DATABASES = "/api/proxy/lightning-database/databases"

# TODO(registration): no confirmed endpoint creates a datasource or points Zetaris at
# a local Parquet path, so in-place registration cannot be implemented honestly. Until
# one is confirmed, register_sources(method="in_place") raises NotConfigured and the
# default path loads the four tables through PATH_RUN instead.
REQUEST_TIMEOUT = 600.0  # zetaris_sql.py uses timeout=600 for the REST channel
UI_DEFAULT = "http://localhost:3000"

LINK_COLUMNS = ("link_type", "log_evidence_id", "change_evidence_id", "score_hint", "detail")
TABLES = ("runs", "changes", "log_errors", "dependencies")
INSERT_BATCH = 50


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


class ZetarisEngine:
    engine_id = "zetaris"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._client: httpx.Client | None = None
        self._ui_token: str | None = None
        self.method = ""
        self.limit = 1000

    # ------------------------------------------------------------------ setup

    @property
    def rest_mode(self) -> bool:
        return bool(self.settings.zetaris_token)

    @property
    def configured(self) -> bool:
        if self.rest_mode:
            return bool(self.settings.zetaris_url)
        return bool(
            self.settings.zetaris_url
            and self.settings.zetaris_user
            and self.settings.zetaris_password
        )

    def _base(self) -> str:
        default = REST_BASE_DEFAULT if self.rest_mode else UI_DEFAULT
        return (self.settings.zetaris_url or default).rstrip("/")

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=REQUEST_TIMEOUT, transport=self._transport, follow_redirects=True
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        if self.rest_mode:
            return {
                "Authorization": f"Bearer {self.settings.zetaris_token}",
                "X-Org-ID": self.settings.zetaris_org,
                "X-Request-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self._ui_token or ''}",
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _login(self) -> str:
        try:
            response = self._http().post(
                f"{self._base()}{PATH_UI_LOGIN}",
                json={
                    "username": self.settings.zetaris_user,
                    "password": self.settings.zetaris_password,
                },
                headers={"Content-Type": "application/json", "X-Request-ID": str(uuid.uuid4())},
            )
        except httpx.HTTPError as exc:
            raise NotConfigured(f"Zetaris UI unreachable at {self._base()}: {exc}") from exc
        if response.status_code >= 400:
            raise NotConfigured(f"Zetaris login failed: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NotConfigured("Zetaris login returned a non-JSON body") from exc
        if isinstance(payload, dict):
            for key in ("idToken", "token", "accessToken"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    self._ui_token = value
                    return value
        match = re.search(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", json.dumps(payload))
        if not match:
            raise NotConfigured("Zetaris login did not return an access token")
        self._ui_token = match.group(0)
        return self._ui_token

    # ------------------------------------------------------------------ status

    def status(self) -> str:
        """online | unauthorized | offline. Never raises."""
        if not self.configured:
            return "offline"
        try:
            if self.rest_mode:
                response = self._http().get(
                    f"{self._base()}{PATH_DATASOURCES}", headers=self._headers()
                )
            else:
                self._login()
                response = self._http().get(
                    f"{self._base()}{PATH_UI_DATABASES}", headers=self._headers()
                )
        except (httpx.HTTPError, NotConfigured):
            return "offline"
        if response.status_code in (401, 403):
            return "unauthorized"
        return "online" if response.status_code < 400 else "offline"

    # ------------------------------------------------------------------ query

    def _qualify(self, sql: str) -> str:
        """Prefix the four table names with ZETARIS_CATALOG, leaving literals alone."""
        catalog = (self.settings.zetaris_catalog or "").strip()
        if not catalog:
            return sql
        parts = re.split(r"('(?:[^']|'')*')", sql)
        for index, part in enumerate(parts):
            if part.startswith("'"):
                continue
            for table in TABLES:
                part = re.sub(rf"(?<![\w.]){table}\b", f"{catalog}.{table}", part)
            parts[index] = part
        return "".join(parts)

    def _rows(self, payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        columns = self._column_names(payload)
        rows: list[dict] = []
        for entry in data:
            if isinstance(entry, dict):
                rows.append(entry)
            elif isinstance(entry, (list, tuple)):
                if columns and len(columns) == len(entry):
                    rows.append(dict(zip(columns, entry)))
                elif len(entry) == len(LINK_COLUMNS):
                    rows.append(dict(zip(LINK_COLUMNS, entry)))
                else:
                    # e.g. SELECT 1: do not pretend it is a link row.
                    rows.append({f"col_{index + 1}": value for index, value in enumerate(entry)})
        return rows

    def _column_names(self, payload: dict) -> list[str]:
        """Column metadata if the response carries any; TODO: shape unverified."""
        for key in ("columns", "fields", "schema"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                names: list[str] = []
                for item in value:
                    if isinstance(item, str):
                        names.append(item)
                    elif isinstance(item, dict):
                        names.append(str(item.get("name") or item.get("label") or item.get("fieldName") or ""))
                if all(names):
                    return names
        return []

    def query(self, sql: str) -> QueryResult:
        if not self.configured:
            raise NotConfigured(
                "Zetaris is not configured: set ZETARIS_URL plus ZETARIS_TOKEN, "
                "or ZETARIS_USER and ZETARIS_PASSWORD."
            )
        statement = self._qualify(sql)
        started = time.perf_counter()
        if self.rest_mode:
            body: dict[str, Any] = {
                "queryId": str(uuid.uuid4()),
                "sql": statement,
                "source": "SqlEditor",
                "limit": self.limit,
            }
            path = PATH_RUN
        else:
            if not self._ui_token:
                self._login()
            body = {"sql": statement, "limit": self.limit}
            path = PATH_UI_RUN

        response = self._post(path, body)
        if response.status_code >= 400:
            raise QueryFailed(
                f"zetaris POST {path}: HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise QueryFailed(f"zetaris returned non-JSON from {path}") from exc

        rows = self._rows(payload)
        return QueryResult(
            rows=rows,
            row_count=len(rows),
            latency_ms=int((time.perf_counter() - started) * 1000),
            sql_sha256=hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        )

    # -------------------------------------------------------------- registration

    def register_sources(
        self, trace_id: str, table_paths: dict[str, Path], method: str = "auto"
    ) -> str:
        if not self.configured:
            raise NotConfigured("Zetaris is not configured; cannot register sources.")
        if method == "in_place":
            raise NotConfigured(
                "No confirmed Zetaris endpoint creates a datasource, so in-place Parquet "
                "registration is not implemented. See TODO(registration) in adapters/zetaris.py."
            )
        self._load_tables(table_paths)
        self.method = "loaded_table"
        return self.method

    def _load_tables(self, table_paths: dict[str, Path]) -> None:
        """Fallback: read the Parquet files locally and insert the rows through SQL."""
        connection = duckdb.connect()
        try:
            for table in TABLES:
                path = table_paths.get(table)
                if path is None:
                    continue
                columns = SCHEMAS[table]
                qualified = self._qualify(table)
                ddl = ", ".join(f"{name} {kind}" for name, kind in columns)
                self._run_sql(f"CREATE TABLE IF NOT EXISTS {qualified} ({ddl})")

                rows = connection.execute(
                    f"SELECT * FROM read_parquet('{str(path).replace(chr(39), chr(39) * 2)}')"
                ).fetchall()
                for start in range(0, len(rows), INSERT_BATCH):
                    chunk = rows[start : start + INSERT_BATCH]
                    values = ", ".join(
                        "(" + ", ".join(_sql_literal(value) for value in row) + ")" for row in chunk
                    )
                    self._run_sql(f"INSERT INTO {qualified} VALUES {values}")
        finally:
            connection.close()

    def _run_sql(self, statement: str) -> None:
        if self.rest_mode:
            body: dict[str, Any] = {
                "queryId": str(uuid.uuid4()),
                "sql": statement,
                "source": "SqlEditor",
                "limit": self.limit,
            }
            path = PATH_RUN
        else:
            if not self._ui_token:
                self._login()
            body = {"sql": statement, "limit": self.limit}
            path = PATH_UI_RUN
        response = self._post(path, body)
        if response.status_code >= 400:
            raise QueryFailed(
                f"zetaris {path}: HTTP {response.status_code} {response.text[:200]}"
            )

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        """A network failure is a QueryFailed, not a raw httpx exception."""
        try:
            return self._http().post(f"{self._base()}{path}", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise QueryFailed(f"zetaris unreachable at {self._base()}: {exc}") from exc

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
