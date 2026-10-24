"""The LLM adapter: provider selection, lane routing, headers, and graceful status."""

import json
from pathlib import Path

import httpx
import pytest

from redrun.adapters.llm import LLMClient, LLMError
from redrun.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def sample_settings() -> Settings:
    return Settings(sample_mode=True, samples_dir=SAMPLES)


def meterless_settings() -> Settings:
    return Settings(
        sample_mode=False,
        llm_provider="meterless",
        meterless_base_url="http://127.0.0.1:8088/v1",
        meterless_runtime_token="rt-token",
        meterless_fast_model="nvidia/fast-model",
        meterless_deep_model="nvidia/deep-model",
    )


def handler_for(seen: list[httpx.Request], payload: dict, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=payload)

    return handler


# ---------------------------------------------------------------- sample provider


def test_sample_provider_reads_the_fixture_and_never_touches_the_network() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"sample mode made a network call: {request.url}")

    client = LLMClient(sample_settings(), sample_case="dependency_bump", transport=httpx.MockTransport(explode))
    got = client.complete([{"role": "user", "content": "why did it fail?"}], lane="deep")

    expected = json.loads((SAMPLES / "dependency_bump" / "llm" / "deep.json").read_text())
    assert got.text == expected["text"]
    assert got.usage.prompt_tokens == expected["usage"]["prompt_tokens"]
    assert got.usage.completion_tokens == expected["usage"]["completion_tokens"]
    assert got.usage.provider == "sample"
    assert got.usage.model == "sample/deep"


def test_sample_status_is_sample() -> None:
    assert LLMClient(sample_settings(), sample_case="bad_commit").status() == "sample"


def test_sample_lane_is_deterministic() -> None:
    client = LLMClient(sample_settings(), sample_case="bad_commit")
    first = client.complete([{"role": "user", "content": "x"}], lane="fast")
    second = client.complete([{"role": "user", "content": "y"}], lane="fast")
    assert first.text == second.text


# ---------------------------------------------------------------- meterless


def test_meterless_sends_the_documented_headers_and_lane_model() -> None:
    seen: list[httpx.Request] = []
    payload = {"choices": [{"message": {"content": "a cause"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    client = LLMClient(meterless_settings(), transport=httpx.MockTransport(handler_for(seen, payload)))

    got = client.complete([{"role": "user", "content": "hi"}], lane="fast")

    assert got.text == "a cause"
    assert got.usage.prompt_tokens == 11
    assert got.usage.provider == "meterless"
    assert got.usage.model == "nvidia/fast-model"
    request = seen[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer rt-token"
    assert request.headers["meterless-app"] == "redrun"
    assert request.headers["meterless-scope"] == "user"
    body = json.loads(request.content)
    assert body["model"] == "nvidia/fast-model"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_deep_lane_uses_the_deep_model() -> None:
    seen: list[httpx.Request] = []
    client = LLMClient(
        meterless_settings(),
        transport=httpx.MockTransport(handler_for(seen, {"choices": [{"message": {"content": "x"}}]})),
    )
    client.complete([{"role": "user", "content": "x"}], lane="deep")
    assert json.loads(seen[0].content)["model"] == "nvidia/deep-model"


def test_meterless_timeout_is_180_seconds() -> None:
    client = LLMClient(meterless_settings(), transport=httpx.MockTransport(handler_for([], {})))
    assert client.timeout == 180.0


@pytest.mark.parametrize("code,expected", [(200, "online"), (401, "unauthorized"), (403, "unauthorized")])
def test_status_maps_http_codes(code: int, expected: str) -> None:
    client = LLMClient(
        meterless_settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(code, json={"data": []})),
    )
    assert client.status() == expected


def test_status_is_offline_when_the_runtime_is_unreachable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = LLMClient(meterless_settings(), transport=httpx.MockTransport(refuse))
    assert client.status() == "offline"


def test_status_is_unauthorized_without_a_token_and_makes_no_call() -> None:
    settings = Settings(
        sample_mode=False, llm_provider="meterless", meterless_runtime_token=""
    )

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call the runtime without a token")

    client = LLMClient(settings, transport=httpx.MockTransport(explode))
    assert client.status() == "unauthorized"


def test_status_checks_the_models_endpoint() -> None:
    seen: list[httpx.Request] = []
    client = LLMClient(
        meterless_settings(),
        transport=httpx.MockTransport(handler_for(seen, {"data": [{"id": "m"}]})),
    )
    assert client.status() == "online"
    assert seen[0].url.path == "/v1/models"


def test_a_malformed_response_raises_llm_error() -> None:
    client = LLMClient(
        meterless_settings(),
        transport=httpx.MockTransport(handler_for([], {"unexpected": True})),
    )
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "x"}], lane="fast")


# ---------------------------------------------------------------- nvidia fallback


def test_nvidia_uses_its_own_base_url_and_key() -> None:
    seen: list[httpx.Request] = []
    settings = Settings(sample_mode=False, llm_provider="nvidia", nvidia_api_key="nv-key")
    client = LLMClient(
        settings,
        transport=httpx.MockTransport(handler_for(seen, {"choices": [{"message": {"content": "y"}}]})),
    )
    got = client.complete([{"role": "user", "content": "x"}], lane="fast")
    assert got.usage.provider == "nvidia"
    assert seen[0].url.host == "integrate.api.nvidia.com"
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer nv-key"
    assert got.usage.model == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_unknown_provider_is_rejected_at_construction() -> None:
    with pytest.raises(LLMError):
        LLMClient(Settings(sample_mode=False, llm_provider="mystery"))
