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

import httpx

from redrun.adapters.base import NotConfigured, QueryFailed, QueryResult
from redrun.config import Settings

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

# In-place registration (verified live against local Zetaris 2.4.x):
#   UI login POST /api/auth/login returns idToken, refreshToken and orgId.
#   Every /api/proxy call needs X-Org-ID; without it the call fails 400
#   "Invalid value for: header X-Org-ID (missing)".
#   Lightning rejects plain CREATE TABLE
#   (LightningDdlParseException: no viable alternative at input 'create table'),
#   so rows are never loaded through INSERT. A per trace Lightning database is
#   created and the four Parquet files are registered as FILESTORE tables:
#     CREATE LIGHTNING DATABASE redrun_<trace> DESCRIBE BY 'RedRun evidence'
#     CREATE LIGHTNING FILESTORE TABLE runs FROM redrun_<trace>
#       FORMAT PARQUET OPTIONS (path "<ZETARIS_DATA_ROOT>/<trace>/runs.parquet")
#   The files must live under the driver's local file root, which is what
#   ZETARIS_DATA_ROOT points at (/home/zetaris/customer-data/shared/redrun).
REQUEST_TIMEOUT = 600.0  # zetaris_sql.py uses timeout=600 for the REST channel
UI_DEFAULT = "http://localhost:3000"
DATA_ROOT_DEFAULT = "/home/zetaris/customer-data/shared/redrun"
DATABASE_PREFIX = "redrun_"

LINK_COLUMNS = ("link_type", "log_evidence_id", "change_evidence_id", "score_hint", "detail")
TABLES = ("runs", "changes", "log_errors", "dependencies")
ALREADY_EXISTS = re.compile(r"already[\s_]+exists", re.IGNORECASE)


def database_name(trace_id: str) -> str:
    """One Lightning database per trace, restricted to [a-z0-9_]."""
    safe = re.sub(r"[^a-z0-9_]", "_", trace_id.lower()).strip("_")
    return f"{DATABASE_PREFIX}{safe or 'trace'}"


def _single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class ZetarisEngine:
    engine_id = "zetaris"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._client: httpx.Client | None = None
        self._ui_token: str | None = None
        # orgId from the login response; the proxy rejects calls without X-Org-ID.
        self._ui_org: str | None = None
        # Per trace database set by register_sources, so queries qualify to it.
        self.catalog = ""
        self.method = ""
        self.limit = 1000
        self.last_row_count: int | None = None
        self.ddl: list[str] = []

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
            # The proxy rejects a call without this header, so never send an empty one.
            "X-Org-ID": self._ui_org or self.settings.zetaris_org,
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
            org = payload.get("orgId") or payload.get("org_id") or payload.get("organizationId")
            if isinstance(org, (str, int)) and str(org).strip():
                self._ui_org = str(org).strip()
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
        """Prefix the four table names with the catalog, leaving literals alone.

        The catalog is the per trace database when sources were registered,
        otherwise whatever ZETARIS_CATALOG names.
        """
        catalog = (self.catalog or self.settings.zetaris_catalog or "").strip()
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
        self.last_row_count = len(rows)
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
        """Point Zetaris at this trace's Parquet files as Lightning FILESTORE tables.

        One database per trace, so two investigations never share a table name.
        `method` stays in the signature for callers, but only in_place exists:
        Lightning rejects the plain CREATE TABLE an INSERT based loader needs.
        """
        if not self.configured:
            raise NotConfigured("Zetaris is not configured; cannot register sources.")
        if method not in ("auto", "in_place"):
            raise NotConfigured(
                f"unknown registration method {method!r}; only in_place is supported"
            )

        database = database_name(trace_id)
        self.catalog = database
        self.ddl = []
        self._run_sql(
            f"CREATE LIGHTNING DATABASE {database} "
            f"DESCRIBE BY {_single_quoted('RedRun evidence')}",
            tolerate_exists=True,
        )

        root = (self.settings.zetaris_data_root or DATA_ROOT_DEFAULT).rstrip("/")
        for table in TABLES:
            if table not in table_paths:
                continue
            remote = f"{root}/{trace_id}/{table}.parquet"
            self._run_sql(
                f"CREATE LIGHTNING FILESTORE TABLE {table} FROM {database} "
                f"FORMAT PARQUET OPTIONS (path {json.dumps(remote)})",
                tolerate_exists=True,
            )

        self.method = "in_place"
        return self.method

    def _run_sql(self, statement: str, *, tolerate_exists: bool = False) -> None:
        self.ddl.append(statement)
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
            # Re-registering a trace is normal, so an existing object is not a failure.
            if tolerate_exists and ALREADY_EXISTS.search(response.text):
                return
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
