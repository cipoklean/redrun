"""FastAPI surface: the JSON API, the SSE streams, and the built single page app."""

import json
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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


def _wants_html(request: Request) -> bool:
    """A browser navigating asks for HTML; fetch() sends */* and gets JSON."""
    return "text/html" in request.headers.get("accept", "").lower()


def _sample_meta(samples_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for meta in sorted(samples_dir.glob("*/meta.json")):
        try:
            payload = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        items.append(
            {
                "name": meta.parent.name,
                "title": payload.get("title") or meta.parent.name.replace("_", " "),
                "category": payload.get("category") or "unknown",
                "description": payload.get("description") or "",
                "order": int(payload.get("order") or 100),
            }
        )
    items.sort(key=lambda item: (item["order"], item["name"]))
    return items


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    app = FastAPI(
        title="RedRun",
        version=VERSION,
        description="Explains why a GitHub Actions run failed, with cited evidence.",
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "sample_mode": cfg.sample_mode,
            "version": VERSION,
            "ui": (cfg.frontend_dir / "index.html").exists(),
        }

    @app.get("/provider/status")
    def provider_status() -> dict[str, object]:
        with LLMClient(cfg) as client:
            llm = {
                "provider": client.provider,
                "status": client.status(),
                "base_url": client.base_url or None,
                "models": {
                    "fast": cfg.meterless_fast_model,
                    "deep": cfg.meterless_deep_model,
                    "judge": cfg.meterless_judge_model or cfg.meterless_deep_model,
                },
                # Never the value. Only whether one is configured.
                "token_set": bool(
                    cfg.meterless_runtime_token
                    if client.provider == "meterless"
                    else cfg.nvidia_api_key
                ),
            }
        zetaris = ZetarisEngine(cfg)
        return {
            "llm": llm,
            "zetaris": {
                "status": zetaris.status(),
                "configured": zetaris.configured,
                "enabled": cfg.zetaris_enabled,
                "url": cfg.zetaris_url or None,
            },
            "github": {"token_set": bool(cfg.github_token)},
            "sample_mode": cfg.sample_mode,
            "samples": len(_sample_meta(cfg.samples_dir)),
        }

    @app.get("/provider/test")
    def provider_test() -> dict[str, object]:
        """Wrap the meterless and zetaris checks. Reports status, never secrets."""
        window = replace(cfg, sample_mode=False)
        started = time.perf_counter()
        with LLMClient(window, sample_case=None) as client:
            status = client.status()
            models = client.list_models()
            llm_latency = int((time.perf_counter() - started) * 1000)
            llm = {
                "provider": client.provider,
                "status": status,
                "base_url": client.base_url or None,
                "model_count": len(models),
                "models": models[:20],
                "latency_ms": llm_latency,
                "token_set": bool(
                    window.meterless_runtime_token
                    if client.provider == "meterless"
                    else window.nvidia_api_key
                ),
            }

        engine = ZetarisEngine(window)
        z_started = time.perf_counter()
        z_status = engine.status()
        zetaris = {
            "status": z_status,
            "configured": engine.configured,
            "enabled": window.zetaris_enabled,
            "url": window.zetaris_url or None,
            "latency_ms": int((time.perf_counter() - z_started) * 1000),
        }

        if llm["status"] == "unauthorized":
            verdict = "unauthorized: set a runtime token, or run in sample mode"
        elif llm["status"] == "offline":
            verdict = f"offline: nothing answering at {llm['base_url']}"
        elif llm["status"] == "online":
            verdict = f"online: {llm['model_count']} models in {llm['latency_ms']} ms"
        else:
            verdict = "sample mode: replays recorded responses, no network"
        return {"llm": llm, "zetaris": zetaris, "summary": verdict}

    @app.get("/samples")
    def samples() -> dict[str, object]:
        return {"samples": _sample_meta(cfg.samples_dir)}

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

    def _index() -> FileResponse:
        index = cfg.frontend_dir / "index.html"
        if not index.exists():
            raise HTTPException(
                status_code=404,
                detail="the frontend is not built; run npm run build in frontend/",
            )
        return FileResponse(index)

    @app.post("/run/stream")
    def run_stream(body: RunRequest) -> StreamingResponse:
        """Run once and stream every Event as it happens, then close."""
        queue: Queue = Queue()
        trace_id = uuid.uuid4().hex[:12]

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
                    trace_id=trace_id,
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
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                # The client needs the id before the run ends, to own its URL.
                "X-RedRun-Trace": trace_id,
            },
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

    @app.get("/run/{trace_id}")
    def stored_run(trace_id: str, request: Request):
        """The stored result for a finished run. HTML in a browser, JSON otherwise."""
        if _wants_html(request):
            return _index()
        path = cfg.data_dir / trace_id / "result.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"no stored result for {trace_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="stored result is not valid JSON") from exc
        return JSONResponse(payload)

    assets = cfg.frontend_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def single_page(full_path: str, request: Request):
        """Client side routes and static files, with API paths left alone."""
        if full_path.split("/")[0] in {"health", "provider", "samples", "assets"} or full_path == "run":
            raise HTTPException(status_code=404, detail="not found")
        if _wants_html(request) or not full_path:
            candidate = cfg.frontend_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return _index()
        candidate = cfg.frontend_dir / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="not found")

    return app


app = create_app()
