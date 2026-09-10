"""The local web interface: `lazyplugin serve`.

Deliberately small. Builds run in a background thread and the page polls for
progress, which survives a laptop sleeping mid-compile far better than a long
streaming connection would, and keeps the whole server under 150 lines.

Binds to 127.0.0.1 by default ON PURPOSE: the process holds your API keys, so
anyone who can reach this port can spend your tokens. Pass --host 0.0.0.0 only
on a network you trust.
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field

JOBS: dict[str, "Job"] = {}


@dataclass
class Job:
    id: str
    status: str = "running"          # running | done | error
    events: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    jar_path: str | None = None


def _run(job: Job, payload: dict) -> None:
    from ..core import forge

    try:
        res = forge(
            payload["request"],
            mc_version=payload.get("version") or "1.21",
            model=payload.get("model") or None,
            provider=payload.get("provider") or None,
            max_fix_rounds=int(payload.get("fix_rounds") or 2),
            on_event=job.events.append,
        )
        job.jar_path = res.jar_path
        job.result = {
            "ok": res.ok,
            "project_dir": res.project_dir,
            "jar": os.path.basename(res.jar_path) if res.jar_path else None,
            "files": res.files,
            "attempts": res.attempts,
            "calls": res.usage.calls,
            "prompt_tokens": res.usage.prompt_tokens,
            "completion_tokens": res.usage.completion_tokens,
            "cost_usd": res.usage.cost_usd,
            "duration_s": res.duration_s,
            "compile_log_tail": res.compile_log_tail[-4000:] if not res.ok else "",
        }
        job.status = "done"
    except Exception as exc:                                   # noqa: BLE001
        job.error = f"{type(exc).__name__}: {exc}"
        job.status = "error"


def create_app():
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    app = FastAPI(title="LazyPlugin", docs_url=None, redoc_url=None)
    here = os.path.dirname(__file__)

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open(os.path.join(here, "index.html"), encoding="utf-8") as f:
            return f.read()

    @app.get("/api/models")
    def models():
        from ..models import MODELS, PROVIDERS
        return {
            "providers": [
                {"id": p.id, "label": p.label, "local": p.local,
                 "configured": p.configured, "env_key": p.env_key, "docs": p.docs}
                for p in PROVIDERS.values()
            ],
            "models": [
                {"id": m.id, "provider": m.provider, "label": m.label,
                 "price_in": m.price_in, "price_out": m.price_out, "notes": m.notes}
                for m in MODELS
            ],
        }

    @app.get("/api/check")
    def check():
        from ..models import check as probe
        return {"rows": probe()}

    @app.post("/api/build")
    def build(payload: dict):
        if not (payload.get("request") or "").strip():
            raise HTTPException(400, "describe what the plugin should do")
        job = Job(id=uuid.uuid4().hex[:12])
        JOBS[job.id] = job
        threading.Thread(target=_run, args=(job, payload), daemon=True).start()
        return {"job_id": job.id}

    @app.get("/api/job/{job_id}")
    def job_status(job_id: str):
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "unknown job")
        return {"status": job.status, "events": job.events,
                "result": job.result, "error": job.error}

    @app.get("/api/download/{job_id}")
    def download(job_id: str):
        job = JOBS.get(job_id)
        if not job or not job.jar_path or not os.path.exists(job.jar_path):
            raise HTTPException(404, "no jar for this job")
        return FileResponse(job.jar_path, filename=os.path.basename(job.jar_path),
                            media_type="application/java-archive")

    return app


def serve(host: str = "127.0.0.1", port: int = 8321, open_browser: bool = True) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The web interface needs a couple of extra packages:\n"
              "    pip install lazyplugin[web]\n"
              "  (or: pip install fastapi uvicorn)")
        return 2

    url = f"http://{host}:{port}"
    print(f"LazyPlugin is running at {url}")
    if host not in ("127.0.0.1", "localhost"):
        print("  WARNING: bound to a public interface. Anyone who can reach this\n"
              "  port can spend your API credits.")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
    return 0
