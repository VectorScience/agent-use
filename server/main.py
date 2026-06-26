"""FastAPI bridge between Cursor (via CDP + transcript files) and any client.

Run with:
    uv run python -m server.main
    # or with auto-reload:
    uv run python -m server.main --reload

Endpoints
---------
- ``GET  /api/health``           — basic liveness probe
- ``GET  /api/status``           — Cursor + CDP + workbench status snapshot
- ``GET  /api/windows``          — list of Cursor workbench windows
- ``GET  /api/screenshot.jpg``   — current workbench screenshot (image/jpeg)
- ``GET  /api/sessions``         — list of transcript sessions (newest first)
- ``GET  /api/sessions/{id}``    — full transcript
- ``WS   /ws``                   — bidirectional: server pushes status+screenshots,
                                  client sends commands {action, ...}

Auth
----
Binds to 0.0.0.0 so a phone on the same LAN can connect. By default NO auth —
assume trusted home network. If you expose this beyond your LAN, set
``CURSOR_REMOTE_TOKEN`` env var; clients must then send ``Authorization: Bearer
<token>`` on HTTP and ``token`` field on the first WS frame.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Make sibling modules importable when running as ``python -m server.main``
# from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cursor_cdp as cdp
import transcripts as tx
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

LOG = logging.getLogger("server")

AUTH_TOKEN = os.environ.get("CURSOR_REMOTE_TOKEN") or None


# --------------------------------------------------------------------------- #
# Config (set once at startup, immutable thereafter)
# --------------------------------------------------------------------------- #
class Config:
    cdp_base: str = "http://127.0.0.1:9222"
    project_path: str | None = None
    screenshot_interval: float = 1.5  # seconds
    status_interval: float = 1.0
    screenshot_quality: int = 70
    screenshot_max_width: int = 1280


CONFIG = Config()


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _check_auth(authorization: str | None) -> None:
    if not AUTH_TOKEN:
        return
    expected = f"Bearer {AUTH_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


# --------------------------------------------------------------------------- #
# Background pollers — push state to all WS clients
# --------------------------------------------------------------------------- #
class ConnectionManager:
    """Tracks connected WebSocket clients and broadcasts JSON payloads.

    Accepting the WS handshake is intentionally NOT done here — FastAPI's
    route layer owns that (see ``websocket_endpoint``), because the auth path
    needs to receive a token frame before accepting. Keeping the manager
    focused on connected sockets only is both simpler and avoids double-accept
    bugs.
    """

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        """Register an already-accepted socket."""
        async with self._lock:
            self.active.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self.active.discard(ws)

    async def broadcast_json(self, payload: dict) -> None:
        """Send to every connected client; drop any that fail."""
        if not self.active:
            return
        text = _json_dumps(payload)
        dead: list[WebSocket] = []
        tasks = [ws.send_text(text) for ws in self.active]
        for ws, result in zip(self.active, await asyncio.gather(*tasks, return_exceptions=True)):
            if isinstance(result, Exception):
                dead.append(ws)
        for ws in dead:
            await self.remove(ws)


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


manager = ConnectionManager()


async def _to_thread(fn, *args, **kwargs):
    """Run a sync blocking function in a worker thread, awaitable."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def status_poller() -> None:
    """Periodically read Cursor status and broadcast diffs.

    We only broadcast when something changed (status JSON hash) — keeps the
    WS quiet when nothing's happening, saves phone battery.
    """
    last_signature: str | None = None
    while True:
        try:
            status = await _to_thread(cdp.read_status, CONFIG.cdp_base)
            payload = status.to_dict()
            signature = _json_dumps(payload)
            if signature != last_signature:
                last_signature = signature
                await manager.broadcast_json({"type": "status", "data": payload})
        except Exception as exc:
            LOG.warning("status poller error: %s", exc)
        await asyncio.sleep(CONFIG.status_interval)


async def screenshot_poller() -> None:
    """Capture screenshots on a slower cadence and push JPEG data URLs.

    We always send the first frame quickly so a freshly connected client sees
    something, then throttle. Clients can also request an immediate refresh
    over WS with ``{action:"refresh_screenshot"}``.
    """
    last_data_url: str | None = None
    while True:
        try:
            img_bytes = await _to_thread(
                cdp.capture_screenshot,
                CONFIG.cdp_base,
                quality=CONFIG.screenshot_quality,
                max_width=CONFIG.screenshot_max_width,
            )
            if img_bytes:
                data_url = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("ascii")
                if data_url != last_data_url:
                    last_data_url = data_url
                    await manager.broadcast_json({"type": "screenshot", "data": data_url})
            else:
                # CDP unreachable — tell clients so they render the placeholder.
                await manager.broadcast_json({"type": "screenshot", "data": None})
        except Exception as exc:
            LOG.warning("screenshot poller error: %s", exc)
        await asyncio.sleep(CONFIG.screenshot_interval)


# --------------------------------------------------------------------------- #
# Command models
# --------------------------------------------------------------------------- #
class ComposerInput(BaseModel):
    text: str
    mode: str = "replace"  # "replace" | "append"


class WindowTarget(BaseModel):
    window_title: str | None = None


# --------------------------------------------------------------------------- #
# App lifecycle
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.info(
        "starting server cdp=%s project=%s auth=%s",
        CONFIG.cdp_base, CONFIG.project_path, "on" if AUTH_TOKEN else "off",
    )
    tasks = [
        asyncio.create_task(status_poller(), name="status_poller"),
        asyncio.create_task(screenshot_poller(), name="screenshot_poller"),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(
    title="Cursor Remote",
    version="1.0.0",
    description="Control Cursor from your phone via CDP.",
    lifespan=lifespan,
)

# Permissive CORS — typical use is phone browser hitting PC over LAN, the
# Tauri shell, or localhost dev with Vite. We're on a trusted network.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
async def get_status(authorization: str | None = Header(default=None)) -> dict:
    _check_auth(authorization)
    status = await _to_thread(cdp.read_status, CONFIG.cdp_base)
    payload = status.to_dict()
    payload["latest_session"] = tx.latest_transcript_summary(CONFIG.project_path)
    return payload


@app.get("/api/windows")
async def get_windows(authorization: str | None = Header(default=None)) -> dict:
    _check_auth(authorization)
    windows = await _to_thread(cdp.list_windows, CONFIG.cdp_base)
    return {"windows": windows}


@app.get("/api/screenshot.jpg")
async def get_screenshot(authorization: str | None = Header(default=None)) -> Response:
    _check_auth(authorization)
    img = await _to_thread(
        cdp.capture_screenshot,
        CONFIG.cdp_base,
        quality=CONFIG.screenshot_quality,
        max_width=CONFIG.screenshot_max_width,
    )
    if not img:
        return Response(status_code=503, content=b"")
    return Response(content=img, media_type="image/jpeg")


@app.get("/api/sessions")
async def list_sessions(authorization: str | None = Header(default=None)) -> dict:
    _check_auth(authorization)
    summaries: list[dict] = []
    for t in tx.iter_all_transcripts(CONFIG.project_path):
        summaries.append(t.to_summary())
    return {"sessions": summaries}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
    _check_auth(authorization)
    transcript = tx.get_transcript(session_id, CONFIG.project_path)
    if not transcript:
        raise HTTPException(status_code=404, detail="session not found")
    return JSONResponse(content=transcript.to_dict())


@app.post("/api/action/send")
async def action_send(
    body: WindowTarget | None = None, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    window_title = body.window_title if body else None
    result = await _to_thread(cdp.click_send, CONFIG.cdp_base, window_title=window_title)
    return result


@app.post("/api/action/stop")
async def action_stop(
    body: WindowTarget | None = None, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    window_title = body.window_title if body else None
    result = await _to_thread(cdp.click_stop, CONFIG.cdp_base, window_title=window_title)
    return result


@app.post("/api/action/compose")
async def action_compose(
    body: ComposerInput, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    result = await _to_thread(
        cdp.set_composer_text, CONFIG.cdp_base, body.text, mode=body.mode
    )
    return result


# --------------------------------------------------------------------------- #
# WebSocket API
# --------------------------------------------------------------------------- #
async def _handle_command(data: dict) -> dict:
    """Run a client command and return a result envelope."""
    action = data.get("action")
    if action == "send":
        result = await _to_thread(cdp.click_send, CONFIG.cdp_base)
        return {"type": "result", "action": "send", "data": result}
    if action == "stop":
        result = await _to_thread(cdp.click_stop, CONFIG.cdp_base)
        return {"type": "result", "action": "stop", "data": result}
    if action == "compose":
        text = str(data.get("text", ""))
        mode = str(data.get("mode", "replace"))
        result = await _to_thread(cdp.set_composer_text, CONFIG.cdp_base, text, mode=mode)
        return {"type": "result", "action": "compose", "data": result}
    if action == "refresh_screenshot":
        # Best effort — the poller will pick it up next tick anyway.
        return {"type": "result", "action": "refresh_screenshot", "data": {"ok": True}}
    if action == "status":
        status = await _to_thread(cdp.read_status, CONFIG.cdp_base)
        return {"type": "status", "data": status.to_dict()}
    return {"type": "error", "error": f"unknown action: {action}"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    # Always accept first. If a token is configured we read the first frame
    # immediately and close on mismatch; otherwise we just register the socket.
    await ws.accept()

    if AUTH_TOKEN:
        try:
            first = await asyncio.wait_for(ws.receive_text(), timeout=5)
            import json

            obj = json.loads(first)
            if obj.get("token") != AUTH_TOKEN:
                await ws.close(code=4401)
                return
        except Exception:
            await ws.close(code=4400)
            return

    await manager.add(ws)
    # Send an immediate status so the client UI hydrates fast.
    try:
        status = await _to_thread(cdp.read_status, CONFIG.cdp_base)
        await ws.send_text(_json_dumps({"type": "status", "data": status.to_dict()}))
    except Exception as exc:
        LOG.warning("initial status push failed: %s", exc)

    try:
        while True:
            text = await ws.receive_text()
            import json

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                await ws.send_text(_json_dumps({"type": "error", "error": "invalid json"}))
                continue
            # Run command in background so we don't block other clients' reads.
            asyncio.create_task(_run_and_send(ws, data))
    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove(ws)


async def _run_and_send(ws: WebSocket, data: dict) -> None:
    try:
        result = await _handle_command(data)
        await ws.send_text(_json_dumps(result))
    except Exception as exc:
        await ws.send_text(_json_dumps({"type": "error", "error": str(exc)}))


# --------------------------------------------------------------------------- #
# Static frontend (Tauri dev server proxies to Vite; production serves dist/)
# --------------------------------------------------------------------------- #
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
else:
    # In dev, point your browser at the Vite dev server (default 5173) which
    # proxies /api and /ws to this FastAPI instance via vite.config.ts.
    @app.get("/")
    async def root():
        return {
            "name": "Cursor Remote API",
            "frontend": "not built — run `npm run build` in frontend/, or use Vite dev server",
            "api_docs": "/docs",
        }


# --------------------------------------------------------------------------- #
# CLI entry
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cursor Remote FastAPI bridge")
    parser.add_argument("--host", default="0.0.0.0", help="bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    parser.add_argument("--cdp-port", type=int, default=9222, help="Cursor CDP port")
    parser.add_argument(
        "--project",
        default=None,
        help="Cursor project path (auto-detect if omitted)",
    )
    parser.add_argument("--reload", action="store_true", help="enable uvicorn reload (dev)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    CONFIG.cdp_base = f"http://127.0.0.1:{args.cdp_port}"
    CONFIG.project_path = args.project

    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="debug" if args.verbose else "info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
