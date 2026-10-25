"""Recording through Meterless/Nightshift, with the model choice written down.

A recording is evidence, so what produced it has to be recoverable from the sample
alone: which provider answered, which model it served, and which routing headers
were used. The token is never part of that.

The deep lane is probed before the batch starts. If it will not answer, both lanes
run on the fast model and the plan records why. That is a substitution, so it is
written into every sample it affects rather than left for a reader to notice.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from redrun.adapters.llm import LLMClient, LLMError
from redrun.config import Settings

# Pinned, so a re-record cannot quietly run on whatever .env happens to name.
FAST_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEEP_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
# The fallback for both lanes when the deep one will not answer.
FALLBACK_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

PROVIDER = "meterless"
PROBE_PROMPT = "Reply with the single word: ready"


class NightshiftError(RuntimeError):
    """The recording asked for Nightshift and the environment is not Nightshift."""


@dataclass(frozen=True)
class LanePlan:
    """Which provider and models this recording will use, and why."""

    provider: str
    fast: str
    deep: str
    fallback: bool
    reason: str
    headers: dict[str, str]

    def model_for(self, lane: str) -> str:
        return self.deep if lane == "deep" else self.fast

    def apply(self, settings: Settings) -> Settings:
        """Return settings that really use this plan, rather than only describing it."""
        return replace(
            settings,
            llm_provider=self.provider,
            meterless_fast_model=self.fast,
            meterless_deep_model=self.deep,
            meterless_judge_model=self.deep,
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "fast_model": self.fast,
            "deep_model": self.deep,
            "deep_lane_fallback": self.fallback,
            "reason": self.reason,
            "headers": dict(self.headers),
        }


def probe_deep(settings: Settings) -> tuple[bool, str]:
    """One tiny deep-lane call. Returns (answered, reason when it did not)."""
    client = LLMClient(settings, sample_case=None)
    try:
        completion = client.complete([{"role": "user", "content": PROBE_PROMPT}], lane="deep")
    except LLMError as exc:
        return False, str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - an unreachable runtime is a fallback, not a crash
        return False, f"{type(exc).__name__}: {exc}"[:300]
    if not str(completion.text).strip():
        return False, "the deep lane answered with nothing"
    return True, ""


def plan_for(
    settings: Settings,
    *,
    lane_probe: Callable[[Settings], tuple[bool, str]] | None = None,
) -> LanePlan:
    """Pin both lanes, then let the deep lane decide whether it is in the plan.

    Raises NightshiftError when the provider is not meterless, because a recording
    that asks for Nightshift and silently runs on a direct provider is a recording
    whose provenance is worse than no recording.
    """
    if settings.llm_provider != PROVIDER:
        raise NightshiftError(
            f"this recording asks for Nightshift: set LLM_PROVIDER={PROVIDER} in .env, "
            f"not {settings.llm_provider!r}"
        )

    headers = {"meterless-app": "redrun", "meterless-scope": "user"}
    pinned = replace(
        settings,
        meterless_fast_model=FAST_MODEL,
        meterless_deep_model=DEEP_MODEL,
        meterless_judge_model=DEEP_MODEL,
    )

    answered, reason = (lane_probe or probe_deep)(pinned)
    if answered:
        return LanePlan(
            provider=PROVIDER,
            fast=FAST_MODEL,
            deep=DEEP_MODEL,
            fallback=False,
            reason=f"{DEEP_MODEL} answered a probe call",
            headers=headers,
        )
    return LanePlan(
        provider=PROVIDER,
        fast=FALLBACK_MODEL,
        deep=FALLBACK_MODEL,
        fallback=True,
        reason=f"{DEEP_MODEL} did not answer its probe ({reason}), so both lanes use {FALLBACK_MODEL}",
        headers=headers,
    )


def describe(plan: LanePlan) -> str:
    """One line for the console, before anything is recorded."""
    if plan.fallback:
        return f"nightshift: fast={plan.fast} deep={plan.deep} (fallback: {plan.reason})"
    return f"nightshift: fast={plan.fast} deep={plan.deep}"
