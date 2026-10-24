"""FastAPI surface: POST /run, GET /health, GET /provider/status."""

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from redrun.adapters.base import EngineError
from redrun.adapters.llm import LLMClient
from redrun.adapters.zetaris import ZetarisEngine
from redrun.collectors.base import CollectorError
from redrun.config import Settings, load_settings
from redrun.core.models import RunResult
from redrun.pipeline import investigate

VERSION = "0.1.0"


class RunRequest(BaseModel):
    target: str | None = None
    sample: str | None = None
    engine: Literal["zetaris", "duckdb"] | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    app = FastAPI(
        title="RedRun",
        version=VERSION,
        description="Explains why a GitHub Actions run failed, with cited evidence.",
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "sample_mode": cfg.sample_mode, "version": VERSION}

    @app.get("/provider/status")
    def provider_status() -> dict[str, object]:
        with LLMClient(cfg) as client:
            llm = {"provider": client.provider, "status": client.status()}
        zetaris = ZetarisEngine(cfg)
        return {
            "llm": llm,
            "zetaris": {
                "status": zetaris.status(),
                "configured": zetaris.configured,
                "enabled": cfg.zetaris_enabled,
                "url": cfg.zetaris_url or None,
            },
        }

    @app.post("/run", response_model=RunResult)
    def run(body: RunRequest) -> RunResult:
        try:
            return investigate(
                body.target, sample=body.sample, settings=cfg, engine=body.engine
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CollectorError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except EngineError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_app()
