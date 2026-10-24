"""Environment settings. One loader, so every entry point agrees on defaults."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(os.getenv("REDRUN_ROOT") or Path(__file__).resolve().parents[1])

_TRUE = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE


@dataclass(frozen=True)
class Settings:
    sample_mode: bool = False
    llm_provider: str = "meterless"
    meterless_base_url: str = "http://127.0.0.1:8088/v1"
    meterless_runtime_token: str = ""
    meterless_fast_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    meterless_deep_model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    meterless_judge_model: str = ""
    nvidia_api_key: str = ""
    github_token: str = ""
    zetaris_enabled: bool = False
    zetaris_url: str = ""
    zetaris_user: str = ""
    zetaris_password: str = ""
    zetaris_token: str = ""
    zetaris_catalog: str = ""
    zetaris_org: str = "1"
    investigator_chunk_tokens: int = 3000
    investigator_max_steps: int = 6
    investigator_carryover_tokens: int = 800
    samples_dir: Path = ROOT / "samples"
    logs_dir: Path = ROOT / "logs"
    data_dir: Path = ROOT / "data"


def load_settings() -> Settings:
    """Read the environment. Safe with no .env and no credentials at all."""
    load_dotenv(ROOT / ".env", override=False)
    return Settings(
        sample_mode=_as_bool(os.getenv("SAMPLE_MODE"), default=True),
        llm_provider=(os.getenv("LLM_PROVIDER") or "meterless").strip().lower(),
        meterless_base_url=os.getenv("METERLESS_BASE_URL") or "http://127.0.0.1:8088/v1",
        meterless_runtime_token=os.getenv("METERLESS_RUNTIME_TOKEN") or "",
        meterless_fast_model=os.getenv("METERLESS_FAST_MODEL")
        or "nvidia/nemotron-3.5-lightning-30b-a3b",
        meterless_deep_model=os.getenv("METERLESS_DEEP_MODEL")
        or "nvidia/nemotron-3.5-super-vl-120b-a12b",
        meterless_judge_model=os.getenv("METERLESS_JUDGE_MODEL") or "",
        nvidia_api_key=os.getenv("NVIDIA_API_KEY") or "",
        github_token=os.getenv("GITHUB_TOKEN") or "",
        zetaris_enabled=_as_bool(os.getenv("ZETARIS_ENABLED"), default=False),
        zetaris_url=(os.getenv("ZETARIS_URL") or "").strip(),
        zetaris_user=os.getenv("ZETARIS_USER") or "",
        zetaris_password=os.getenv("ZETARIS_PASSWORD") or "",
        zetaris_token=os.getenv("ZETARIS_TOKEN") or "",
        zetaris_catalog=(os.getenv("ZETARIS_CATALOG") or "").strip(),
        zetaris_org=(os.getenv("ZETARIS_ORG_ID") or "1").strip(),
        investigator_chunk_tokens=int(os.getenv("INVESTIGATOR_CHUNK_TOKENS") or 3000),
        investigator_max_steps=int(os.getenv("INVESTIGATOR_MAX_STEPS") or 6),
        investigator_carryover_tokens=int(os.getenv("INVESTIGATOR_CARRYOVER_TOKENS") or 800),
        logs_dir=Path(os.getenv("REDRUN_LOGS_DIR") or ROOT / "logs"),
        data_dir=Path(os.getenv("REDRUN_DATA_DIR") or ROOT / "data"),
    )
