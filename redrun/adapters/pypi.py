"""Locally written PyPI client. Only the parts RedRun needs, cache handled by the caller."""

import httpx

PYPI_ROOT = "https://pypi.org/pypi"


class PyPIClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout: float = 15.0):
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "redrun/0.2"},
        )

    def get(self, name: str) -> dict | None:
        """The PyPI JSON document for a package, or None if it is unknown/unreachable."""
        try:
            response = self._client.get(f"{PYPI_ROOT}/{name}/json")
        except httpx.HTTPError:
            return None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def close(self) -> None:
        self._client.close()
