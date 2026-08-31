from __future__ import annotations

import json
import threading
from pathlib import Path

from scheduler.models import ScheduledTask

_lock = threading.Lock()
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduled_tasks.json"


def tasks_path() -> Path:
    return _DEFAULT_PATH


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


def load_tasks(path: Path | None = None) -> list[ScheduledTask]:
    p = path or tasks_path()
    with _lock:
        _ensure_file(p)
        raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [ScheduledTask.from_dict(item) for item in raw]


def save_tasks(tasks: list[ScheduledTask], path: Path | None = None) -> None:
    p = path or tasks_path()
    with _lock:
        _ensure_file(p)
        p.write_text(
            json.dumps([t.to_dict() for t in tasks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def get_task(task_id: str, path: Path | None = None) -> ScheduledTask | None:
    for t in load_tasks(path):
        if t.id == task_id:
            return t
    return None


def upsert_task(task: ScheduledTask, path: Path | None = None) -> ScheduledTask:
    tasks = load_tasks(path)
    found = False
    for i, t in enumerate(tasks):
        if t.id == task.id:
            tasks[i] = task
            found = True
            break
    if not found:
        tasks.append(task)
    save_tasks(tasks, path)
    return task


def delete_task(task_id: str, path: Path | None = None) -> bool:
    tasks = load_tasks(path)
    new_tasks = [t for t in tasks if t.id != task_id]
    if len(new_tasks) == len(tasks):
        return False
    save_tasks(new_tasks, path)
    return True
