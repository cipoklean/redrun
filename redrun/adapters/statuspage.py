"""GitHub status incidents, cached and redacted. Cached because the demo may be offline."""

import json
from pathlib import Path

import httpx

from redrun.core.redact import redact_data

INCIDENTS_URL = "https://www.githubstatus.com/api/v2/incidents.json"


class StatusPageClient:
    def __init__(self, cache_dir: Path, *, transport: httpx.BaseTransport | None = None):
        self.cache_dir = Path(cache_dir)
        self._transport = transport

    def incidents(self) -> list[dict]:
        cache = self.cache_dir / "incidents.json"
        if cache.exists():
            try:
                return list(json.loads(cache.read_text()).get("incidents") or [])
            except ValueError:
                cache.unlink(missing_ok=True)

        try:
            with httpx.Client(timeout=10.0, transport=self._transport) as client:
                response = client.get(INCIDENTS_URL)
            if response.status_code >= 400:
                return []
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        clean = redact_data(payload)
        if isinstance(clean, dict):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(clean, indent=2, default=str) + "\n", encoding="utf-8")
            return list(clean.get("incidents") or [])
        return []
