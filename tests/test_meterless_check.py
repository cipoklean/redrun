"""meterless-check: one real completion, or an honest non-zero exit."""

import httpx
import pytest

from redrun.cli import _mask, meterless_check
from redrun.config import Settings

TOKEN = "rt-super-secret-token"


def settings(**overrides) -> Settings:
    base = dict(
        sample_mode=False,
        llm_provider="meterless",
        meterless_base_url="http://127.0.0.1:8088/v1",
        meterless_runtime_token=TOKEN,
        meterless_fast_model="nvidia/nemotron-3.5-lightning-30b-a3b",
    )
    base.update(overrides)
    return Settings(**base)


def handler(status_models=200, chat_status=200, model=None):
    def inner(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                status_models, json={"data": [{"id": "nvidia/fast"}, {"id": "nvidia/deep"}]}
            )
        return httpx.Response(
            chat_status,
            json={
                "model": model or "nvidia/nemotron-3.5-lightning-30b-a3b",
                "choices": [{"message": {"content": "ready"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 1},
            },
        )

    return inner


def test_offline_exits_one_with_a_reason(capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert meterless_check(settings(), transport=httpx.MockTransport(refuse)) == 1
    out = capsys.readouterr().out
    assert "offline" in out
    assert "exit 1" in out


def test_unauthorized_is_named_not_blurred(capsys: pytest.CaptureFixture[str]) -> None:
    assert meterless_check(settings(meterless_runtime_token="")) == 1
    out = capsys.readouterr().out
    assert "unauthorized" in out
    assert "(not set)" in out


def test_a_401_from_the_runtime_is_unauthorized(capsys: pytest.CaptureFixture[str]) -> None:
    client = httpx.MockTransport(handler(status_models=401))
    assert meterless_check(settings(), transport=client) == 1
    assert "unauthorized" in capsys.readouterr().out


def test_online_prints_models_status_latency_and_tokens(capsys: pytest.CaptureFixture[str]) -> None:
    assert meterless_check(settings(), transport=httpx.MockTransport(handler())) == 0
    out = capsys.readouterr().out
    assert "nvidia/fast" in out
    assert "chat HTTP    : 200" in out
    assert "chat model   : nvidia/nemotron-3.5-lightning-30b-a3b" in out
    assert "in=12 out=1" in out
    assert "chat latency" in out


def test_the_token_is_masked_and_never_printed(capsys: pytest.CaptureFixture[str]) -> None:
    assert meterless_check(settings(), transport=httpx.MockTransport(handler())) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert _mask(TOKEN) in out


def test_a_different_model_than_pinned_is_called_out(capsys: pytest.CaptureFixture[str]) -> None:
    transport = httpx.MockTransport(handler(model="nvidia/other-model"))
    assert meterless_check(settings(), transport=transport) == 0
    assert "not the pinned id" in capsys.readouterr().out


def test_a_non_meterless_provider_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    base = settings(llm_provider="nvidia", nvidia_api_key="nv-key")
    assert meterless_check(base, transport=httpx.MockTransport(handler())) == 0
    out = capsys.readouterr().out
    assert "not the Meterless" in out


def test_mask_keeps_first_four_and_last_two() -> None:
    assert _mask("abcdefghij") == "abcd****ij"
    assert _mask("") == "(not set)"
