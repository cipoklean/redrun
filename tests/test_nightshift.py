"""The Nightshift re-record: pinned lanes, a recorded fallback, and no token anywhere.

A recording is evidence, so these tests are about what a reader can recover from the
sample: which provider answered, which models ran, whether the deep lane was replaced,
and that the credential is not in any of it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from redrun.adapters.llm import UNRECORDED, Completion, LLMClient, Usage
from redrun.config import Settings
from redrun.eval import nightshift
from redrun.recording import RecordingLLM

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TOKEN = "tok-123-do-not-record-me"


def settings(**overrides) -> Settings:
    base = Settings(
        sample_mode=False,
        llm_provider="meterless",
        meterless_base_url="http://127.0.0.1:8088/v1",
        meterless_runtime_token=TOKEN,
        meterless_fast_model="whatever/env/says",
        meterless_deep_model="whatever/env/says-too",
    )
    return replace(base, **overrides) if overrides else base


def test_the_plan_refuses_a_provider_that_is_not_nightshift() -> None:
    """A recording that asks for Nightshift must not quietly run on a direct provider."""
    with pytest.raises(nightshift.NightshiftError) as excinfo:
        nightshift.plan_for(settings(llm_provider="nvidia", nvidia_api_key="k"))
    assert "meterless" in str(excinfo.value)


def test_a_dead_deep_lane_falls_back_and_records_why() -> None:
    plan = nightshift.plan_for(settings(), lane_probe=lambda _s: (False, "500 from the runtime"))

    assert plan.fallback is True
    assert plan.fast == plan.deep == nightshift.FALLBACK_MODEL
    record = plan.as_record()
    assert record["deep_lane_fallback"] is True
    assert "500 from the runtime" in record["reason"]


def test_a_working_deep_lane_keeps_the_pinned_models() -> None:
    plan = nightshift.plan_for(settings(), lane_probe=lambda _s: (True, ""))

    assert plan.fallback is False
    assert plan.fast == nightshift.FAST_MODEL
    assert plan.deep == nightshift.DEEP_MODEL
    assert nightshift.FAST_MODEL.startswith("nvidia/nemotron")


def test_the_environment_cannot_override_the_pinned_lanes() -> None:
    plan = nightshift.plan_for(settings(), lane_probe=lambda _s: (True, ""))

    assert plan.fast != "whatever/env/says"
    assert plan.deep != "whatever/env/says-too"


def test_the_plan_is_applied_and_not_only_described() -> None:
    plan = nightshift.plan_for(settings(), lane_probe=lambda _s: (False, "boom"))
    applied = plan.apply(settings())

    assert applied.llm_provider == "meterless"
    assert applied.meterless_deep_model == nightshift.FALLBACK_MODEL
    assert applied.meterless_fast_model == nightshift.FALLBACK_MODEL


def test_no_token_reaches_the_record_or_the_headers() -> None:
    plan = nightshift.plan_for(settings(), lane_probe=lambda _s: (True, ""))
    record = plan.as_record()

    assert TOKEN not in json.dumps(record)
    assert record["headers"] == {"meterless-app": "redrun", "meterless-scope": "user"}
    assert "authorization" not in json.dumps(record).lower()


def test_a_recorded_step_carries_provider_model_and_headers() -> None:
    class Inner:
        request_meta = {"meterless-app": "redrun", "meterless-scope": "user"}
        token = TOKEN

        def complete(self, messages, lane="fast"):
            return Completion(
                text="a reply",
                usage=Usage(
                    prompt_tokens=1,
                    completion_tokens=2,
                    latency_ms=3,
                    provider="meterless",
                    model="nvidia/nemotron-3.5-lightning-30b-a3b",
                ),
            )

    recorder = RecordingLLM(Inner())
    recorder.complete([{"role": "user", "content": "a"}], lane="fast")
    step = recorder.steps[0]

    assert step["usage"]["provider"] == "meterless"
    assert step["usage"]["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert step["headers"]["meterless-scope"] == "user"
    assert TOKEN not in json.dumps(step)


def test_a_sample_recorded_without_the_provider_says_so() -> None:
    """The samples recorded against a provider directly replay, and do not claim one."""
    client = LLMClient(
        Settings(sample_mode=True, samples_dir=SAMPLES), sample_case="dependency_bump"
    )
    got = client.complete([{"role": "user", "content": "step"}], lane="fast")

    assert got.usage.provider == UNRECORDED


def test_the_client_records_only_its_routing_headers() -> None:
    client = LLMClient(settings(), sample_case=None)

    assert client.request_meta == {"meterless-app": "redrun", "meterless-scope": "user"}
    assert TOKEN not in json.dumps(client.request_meta)
