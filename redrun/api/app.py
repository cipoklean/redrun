"""FastAPI surface: POST /run, streaming events, health and provider status."""

import json
import threading
from queue import Empty, Queue
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
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

    @app.post("/run/stream")
    def run_stream(body: RunRequest) -> StreamingResponse:
        """Run once and stream every Event as it happens, then close."""
        queue: Queue = Queue()

        def sink(event: Any) -> None:
            queue.put(event)

        def work() -> None:
            try:
                investigate(
                    body.target,
                    sample=body.sample,
                    settings=cfg,
                    engine=body.engine,
                    sink=sink,
                )
            except (ValueError, CollectorError, EngineError) as exc:
                queue.put({"detail": str(exc)})
            finally:
                queue.put(None)

        threading.Thread(target=work, daemon=True).start()

        def events():
            while True:
                try:
                    item = queue.get(timeout=600)
                except Empty:
                    return
                if item is None:
                    return
                if isinstance(item, dict):
                    yield f"event: error\ndata: {json.dumps(item)}\n\n"
                else:
                    yield f"data: {item.model_dump_json()}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/run/{trace_id}/events")
    def replay_events(trace_id: str) -> StreamingResponse:
        """Replay a finished run's trace as SSE. Read-only, no re-execution."""
        path = cfg.logs_dir / f"{trace_id}.jsonl"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no trace for {trace_id}")

        def events():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    yield f"data: {line}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
