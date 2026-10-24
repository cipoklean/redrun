"""Provider-agnostic LLM client: metered runtime first, NVIDIA direct as fallback, fixtures offline.

The adapter never decides anything. It returns text plus usage, and the caller
stays responsible for what that text is allowed to influence.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from redrun.config import Settings

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
TIMEOUT_SECONDS = 180.0
LANES = ("fast", "deep")


class LLMError(RuntimeError):
    """The provider could not answer, or answered something unusable."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    provider: str
    model: str


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage


class LLMClient:
    """`complete(messages, lane)` returns text plus usage. `status()` is a probe."""

    def __init__(
        self,
        settings: Settings,
        *,
        sample_case: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.sample_case = sample_case
        self.timeout = TIMEOUT_SECONDS
        self.provider = "sample" if settings.sample_mode else settings.llm_provider

        if self.provider == "sample":
            self._client: httpx.Client | None = None
            return

        if self.provider == "meterless":
            base = settings.meterless_base_url.rstrip("/")
            headers = {
                "meterless-app": "redrun",
                "meterless-scope": "user",
                "Authorization": f"Bearer {settings.meterless_runtime_token}",
            }
        elif self.provider == "nvidia":
            base = NVIDIA_BASE_URL
            headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
        else:
            raise LLMError(f"unknown LLM_PROVIDER: {settings.llm_provider!r}")

        self.base_url = base
        self._client = httpx.Client(
            base_url=base,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    # ------------------------------------------------------------------ helpers

    def model_for(self, lane: str) -> str:
        if lane not in LANES:
            raise LLMError(f"unknown lane {lane!r}; expected one of {LANES}")
        if self.provider == "sample":
            return f"sample/{lane}"
        return (
            self.settings.meterless_fast_model
            if lane == "fast"
            else self.settings.meterless_deep_model
        )

    def _sample_completion(self, lane: str) -> Completion:
        if not self.sample_case:
            raise LLMError("sample provider needs a sample case")
        path: Path = self.settings.samples_dir / self.sample_case / "llm" / f"{lane}.json"
        if not path.exists():
            raise LLMError(f"missing sample LLM fixture: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        usage = payload.get("usage") or {}
        return Completion(
            text=str(payload.get("text", "")),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                latency_ms=0,
                provider="sample",
                model=self.model_for(lane),
            ),
        )

    # ------------------------------------------------------------------- public

    def complete(self, messages: list[dict[str, str]], lane: str = "fast") -> Completion:
        if self.provider == "sample":
            return self._sample_completion(lane)
        if self._client is None:
            raise LLMError("client was not initialised")

        model = self.model_for(lane)
        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json={"model": model, "messages": messages, "temperature": 0, "stream": False},
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider} unreachable: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            raise LLMError(
                f"{self.provider} returned {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.provider} returned an unusable body: {response.text[:200]}") from exc

        usage = payload.get("usage") or {}
        return Completion(
            text=str(text),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                latency_ms=latency_ms,
                provider=self.provider,
                model=str(payload.get("model") or model),
            ),
        )

    def status(self) -> str:
        """online | unauthorized | offline | sample. Never raises."""
        if self.provider == "sample":
            return "sample"
        if self.provider == "meterless" and not self.settings.meterless_runtime_token:
            # No token means no honest way to be online; skip a call that cannot succeed.
            return "unauthorized"
        if self._client is None:
            return "offline"
        try:
            response = self._client.get(f"{self.base_url}/models")
        except httpx.HTTPError:
            return "offline"
        if response.status_code in (401, 403):
            return "unauthorized"
        if response.status_code >= 400:
            return "offline"
        return "online"

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def completion_metrics(completions: list[Completion]) -> dict[str, Any]:
    """Fold usage into the RunResult.metrics fields."""
    return {
        "llm_calls": len(completions),
        "tokens_in": sum(c.usage.prompt_tokens for c in completions),
        "tokens_out": sum(c.usage.completion_tokens for c in completions),
        "latency_ms": sum(c.usage.latency_ms for c in completions),
    }
