"""The local web interface: `lazyplugin serve`.

Deliberately small. Builds and test servers run in background threads and the
page polls for progress, which survives a laptop sleeping mid-compile far
better than a long streaming connection would.

Binds to 127.0.0.1 by default ON PURPOSE: the process holds your API keys and
can start a Minecraft server, so anyone who reaches this port can spend your
credits and run a process on your machine. Pass --host 0.0.0.0 only on a
network you trust.
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field

JOBS: dict[str, "Job"] = {}
TESTS: dict[str, "TestRun"] = {}


@dataclass
class Job:
    id: str
    status: str = "running"          # running | done | error
    events: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    jar_path: str | None = None


@dataclass
class TestRun:
    id: str
    status: str = "starting"         # starting | running | stopped | error
    log: list[str] = field(default_factory=list)
    port: int = 25565
    error: str | None = None
    proc: object | None = None


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


def _run_server(test: TestRun, jar: str, version: str, server_dir: str) -> None:
    from .. import testserver as ts

    def line(msg: str):
        test.log.append(msg)
        # Paper announces this once the world is loaded and plugins are enabled.
        if "Done (" in msg and test.status == "starting":
            test.status = "running"
        if len(test.log) > 600:                # a long session should not eat RAM
            del test.log[:200]

    try:
        server_jar = ts.prepare(server_dir, version=version, plugin_jar=jar,
                                accept_eula=True, port=test.port, on_event=line)
        test.proc = ts.start(server_dir, server_jar, on_line=line)
        test.status = "stopped"
    except Exception as exc:                                   # noqa: BLE001
        test.error = f"{type(exc).__name__}: {exc}"
        test.status = "error"


LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]", ""}


def create_app(allowed_hosts: set[str] | None = None):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    app = FastAPI(title="LazyPlugin", docs_url=None, redoc_url=None)
    here = os.path.dirname(__file__)
    allowed = set(allowed_hosts or LOOPBACK_NAMES)

    @app.middleware("http")
    async def only_known_hosts(request, call_next):
        """Reject requests that arrive under an unexpected hostname.

        Without this, an attacker domain whose DNS points at 127.0.0.1 looks
        same-origin to the browser, so any page you happen to visit could drive
        this server: spend your API credits, or start a process on your machine.
        Checking the Host header costs nothing and closes it.
        """
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in allowed:
            return JSONResponse({"detail": f"host {host!r} not allowed"}, status_code=403)
        return await call_next(request)

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

    # --- local test server --------------------------------------------------

    @app.get("/api/java")
    def java():
        """So the page can say what is missing before you click anything."""
        from ..testserver import java_version
        v = java_version()
        return {"version": v, "ok": bool(v and v >= 21)}

    @app.post("/api/test")
    def start_test(payload: dict):
        job = JOBS.get(payload.get("job_id") or "")
        if not job or not job.jar_path:
            raise HTTPException(404, "build a plugin first")
        if not payload.get("accept_eula"):
            # Agreeing to the Minecraft EULA has to be the user act, not ours.
            raise HTTPException(400, "the Minecraft EULA must be accepted first")
        if any(t.status in ("starting", "running") for t in TESTS.values()):
            raise HTTPException(409, "a test server is already running")

        test = TestRun(id=uuid.uuid4().hex[:8],
                       port=int(payload.get("port") or 25565))
        TESTS[test.id] = test
        threading.Thread(
            target=_run_server,
            args=(test, job.jar_path, payload.get("version") or "1.21",
                  payload.get("dir") or "./test-server"),
            daemon=True).start()
        return {"test_id": test.id, "port": test.port}

    @app.get("/api/test/{test_id}")
    def test_status(test_id: str):
        test = TESTS.get(test_id)
        if not test:
            raise HTTPException(404, "unknown test server")
        return {"status": test.status, "log": test.log,
                "port": test.port, "error": test.error}

    @app.post("/api/test/{test_id}/stop")
    def stop_test(test_id: str):
        from ..testserver import stop
        test = TESTS.get(test_id)
        if not test:
            raise HTTPException(404, "unknown test server")
        if test.proc is not None:
            stop(test.proc)
        test.status = "stopped"
        return {"status": test.status}

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
              "  port can spend your API credits and start servers on this machine.")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # Whatever you bound to is legitimate; everything else is not.
    uvicorn.run(create_app(LOOPBACK_NAMES | {host}), host=host, port=port,
                log_level="warning")
    return 0
