"""定时任务 GUI 服务：管理任务 + 后台调度。

启动:
    uv run python -m scheduler.app
    # 浏览器打开 http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cursor_cdp as cdp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scheduler.apps import DEFAULT_CDP_PORTS, get_cdp_base, get_profile, supported_apps
from scheduler.engine import run_task, scheduler_loop
from scheduler.models import ScheduledTask, effective_composer_mode, effective_model, parse_messages
from scheduler.send import send_message, send_message_sequence
from scheduler.store import delete_task, load_tasks, upsert_task

LOG = logging.getLogger("scheduler.app")

GUI_DIR = Path(__file__).resolve().parent.parent / "scheduler_gui"


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    time: str = Field(description="HH:MM")
    message: str = Field(min_length=1, description="单条或多条（用 --- 分隔）")
    app: str = "cursor"
    window_title: str | None = None
    window_mode: str = "auto"
    chat_tab: str | None = None
    composer_mode: str | None = None
    model: str | None = None
    wait_between: bool = True
    wait_timeout_seconds: int = 3600
    enabled: bool = True
    no_click: bool = False


class TaskUpdate(BaseModel):
    name: str | None = None
    time: str | None = None
    message: str | None = None
    app: str | None = None
    window_title: str | None = None
    window_mode: str | None = None
    chat_tab: str | None = None
    composer_mode: str | None = None
    model: str | None = None
    wait_between: bool | None = None
    wait_timeout_seconds: int | None = None
    enabled: bool | None = None
    no_click: bool | None = None


class SendNowBody(BaseModel):
    message: str
    app: str = "cursor"
    window_title: str | None = None
    window_mode: str = "auto"
    chat_tab: str | None = None
    composer_mode: str | None = None
    model: str | None = None
    wait_between: bool = False
    wait_timeout_seconds: int = 3600
    no_click: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scheduler_loop(None), name="scheduler_loop")
    LOG.info("scheduler engine started (per-app cdp routing)")
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Cursor 定时任务", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "apps": supported_apps()}


@app.get("/api/apps")
async def list_apps() -> dict:
    """受控应用列表 + 各自 CDP 状态（GUI 顶栏展示用）。"""
    out = []
    for name in supported_apps():
        base = get_cdp_base(name)
        status = await asyncio.to_thread(cdp.read_status, base, profile=get_profile(name))
        d = status.to_dict()
        d["cdp_port"] = DEFAULT_CDP_PORTS[name]
        out.append(d)
    return {"apps": out}


@app.get("/api/cdp/status")
async def cdp_status(
    app: str = "cursor",
    window_title: str | None = None,
    window_mode: str = "auto",
) -> dict:
    status = await asyncio.to_thread(
        cdp.read_status,
        get_cdp_base(app),
        window_title=window_title,
        window_mode=window_mode,
        profile=get_profile(app),
    )
    return status.to_dict()


@app.get("/api/windows")
async def list_windows(
    app: str = "cursor",
) -> dict:
    base = get_cdp_base(app)
    profile = get_profile(app)
    windows = await asyncio.to_thread(cdp.list_windows, base, profile=profile)
    return {"windows": windows, "cdp_reachable": cdp.is_cdp_reachable(base), "app": profile.name}


@app.get("/api/composer-settings")
async def composer_settings(
    app: str = "cursor",
    window_title: str | None = None,
    window_mode: str = "auto",
) -> dict:
    return await asyncio.to_thread(
        cdp.read_composer_settings,
        get_cdp_base(app),
        window_title=window_title,
        window_mode=window_mode,
        profile=get_profile(app),
    )


@app.get("/api/chat-tabs")
async def chat_tabs(
    app: str = "cursor",
    window_title: str | None = None,
    window_mode: str = "auto",
) -> dict:
    tabs = await asyncio.to_thread(
        cdp.list_chat_tabs,
        get_cdp_base(app),
        window_title=window_title,
        window_mode=window_mode,
        profile=get_profile(app),
    )
    return {"tabs": tabs}


@app.get("/api/tasks")
async def get_tasks() -> dict:
    tasks = load_tasks()
    return {"tasks": [t.to_dict() for t in tasks]}


@app.post("/api/tasks")
async def create_task(body: TaskCreate) -> dict:
    task = ScheduledTask.new(
        name=body.name,
        time=body.time,
        message=body.message,
        app=body.app,
        window_title=body.window_title or None,
        window_mode=body.window_mode,
        chat_tab=body.chat_tab or None,
        composer_mode=body.composer_mode or None,
        model=body.model or None,
        wait_between=body.wait_between,
        wait_timeout_seconds=body.wait_timeout_seconds,
        enabled=body.enabled,
        no_click=body.no_click,
    )
    upsert_task(task)
    return {"task": task.to_dict()}


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate) -> dict:
    tasks = load_tasks()
    for t in tasks:
        if t.id != task_id:
            continue
        if body.name is not None:
            t.name = body.name
        if body.time is not None:
            t.time = body.time
        if body.message is not None:
            t.message = body.message
        if body.app is not None:
            t.app = body.app
        if body.window_title is not None:
            t.window_title = body.window_title or None
        if body.window_mode is not None:
            t.window_mode = body.window_mode
        if body.chat_tab is not None:
            t.chat_tab = body.chat_tab or None
        if body.composer_mode is not None:
            t.composer_mode = body.composer_mode or None
        if body.model is not None:
            t.model = body.model or None
        if body.wait_between is not None:
            t.wait_between = body.wait_between
        if body.wait_timeout_seconds is not None:
            t.wait_timeout_seconds = body.wait_timeout_seconds
        if body.enabled is not None:
            t.enabled = body.enabled
        if body.no_click is not None:
            t.no_click = body.no_click
        upsert_task(t)
        return {"task": t.to_dict()}
    raise HTTPException(status_code=404, detail="task not found")


@app.delete("/api/tasks/{task_id}")
async def remove_task(task_id: str) -> dict:
    if not delete_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}


@app.post("/api/tasks/{task_id}/run")
async def run_task_now(task_id: str) -> dict:
    tasks = load_tasks()
    for t in tasks:
        if t.id == task_id:
            result = await run_task(None, t)
            return {"ok": result.get("ok"), "result": result, "task": t.to_dict()}
    raise HTTPException(status_code=404, detail="task not found")


@app.post("/api/send-now")
async def send_now(body: SendNowBody) -> dict:
    base = get_cdp_base(body.app)
    profile = get_profile(body.app)
    messages = parse_messages(body.message)
    common = {
        "window_title": body.window_title,
        "window_mode": body.window_mode,
        "prefer_foreground": not bool(body.window_title),
        "chat_tab": body.chat_tab,
        "composer_mode": effective_composer_mode(body.composer_mode),
        "model": effective_model(body.model),
        "no_click": body.no_click,
        "profile": profile,
    }
    if len(messages) <= 1:
        result = await asyncio.to_thread(
            send_message,
            base,
            messages[0] if messages else body.message,
            **common,
        )
    else:
        result = await asyncio.to_thread(
            send_message_sequence,
            base,
            messages,
            wait_between=body.wait_between,
            wait_timeout_seconds=body.wait_timeout_seconds,
            **common,
        )
    return result


if GUI_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(GUI_DIR)), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(GUI_DIR / "index.html")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cursor 定时任务 GUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    import uvicorn

    LOG.info("GUI: http://%s:%s (apps: %s)", args.host, args.port, ", ".join(supported_apps()))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
