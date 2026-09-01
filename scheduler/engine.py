"""后台调度引擎：每分钟检查到点的任务并执行。"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Callable

from scheduler.apps import get_cdp_base, get_profile
from scheduler.models import ScheduledTask
from scheduler.store import load_tasks, upsert_task

LOG = logging.getLogger("scheduler.engine")


def _today_key() -> str:
    return dt.date.today().isoformat()


def _parse_hhmm(value: str) -> dt.time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM: {value!r}")
    h, m = int(parts[0]), int(parts[1])
    return dt.time(hour=h, minute=m)


def should_fire(task: ScheduledTask, now: dt.datetime) -> bool:
    """任务是否应在 ``now`` 这一分钟触发（每天一次）。"""
    if not task.enabled:
        return False
    try:
        target_t = _parse_hhmm(task.time)
    except ValueError:
        return False
    if now.hour != target_t.hour or now.minute != target_t.minute:
        return False
    # 同一分钟只跑一次
    if task.last_run_at:
        try:
            last = dt.datetime.fromisoformat(task.last_run_at)
            if last.date() == now.date() and last.hour == now.hour and last.minute == now.minute:
                return False
        except ValueError:
            pass
    return True


async def run_task(
    cdp_base: str | None,
    task: ScheduledTask,
    *,
    on_done: Callable[[ScheduledTask, dict], None] | None = None,
) -> dict:
    from scheduler import apps
    from scheduler.send import send_message_sequence

    base = cdp_base or apps.get_cdp_base(task.app)
    profile = apps.get_profile(task.app)

    # 目标操作任务（resume/clear/edit）：只点按钮，不发送文案
    if task.goal_action:
        result = await asyncio.to_thread(
            apps.run_extra_action, task.app, f"goal_{task.goal_action}"
        )
        result = dict(result)
        result["app"] = profile.name
    else:
        messages = task.messages_list()
        result = await asyncio.to_thread(
            send_message_sequence,
            base,
            messages,
            window_title=task.window_title,
            window_mode=task.window_mode,
            prefer_foreground=not bool(task.window_title),
            chat_tab=task.chat_tab,
            composer_mode=task.composer_mode,
            model=task.model,
            no_click=task.no_click,
            wait_between=task.wait_between,
            wait_timeout_seconds=task.wait_timeout_seconds,
            profile=profile,
        )
    task.last_run_at = dt.datetime.now().isoformat(timespec="seconds")
    task.last_result = "ok" if result.get("ok") else str(result.get("reason", "failed"))
    upsert_task(task)
    if on_done:
        on_done(task, result)
    return result


async def scheduler_loop(
    cdp_base: str | None = None,
    *,
    poll_seconds: float = 20.0,
    on_fire: Callable[[ScheduledTask, dict], None] | None = None,
) -> None:
    """无限循环，检查并执行到点任务。

    ``cdp_base`` 为 None 时按每个任务的 ``app`` 解析各自的 CDP 地址。
    """
    LOG.info("scheduler started cdp=%s poll=%.0fs", cdp_base or "per-app", poll_seconds)
    while True:
        now = dt.datetime.now()
        try:
            for task in load_tasks():
                if should_fire(task, now):
                    LOG.info("firing task %s (%s) at %s", task.id, task.name, task.time)
                    try:
                        result = await run_task(cdp_base, task, on_done=on_fire)
                        if result.get("ok"):
                            LOG.info("task %s ok", task.id)
                        else:
                            LOG.warning("task %s failed: %s", task.id, result.get("reason"))
                    except Exception as exc:
                        LOG.exception("task %s error: %s", task.id, exc)
        except Exception as exc:
            LOG.warning("scheduler tick error: %s", exc)
        await asyncio.sleep(poll_seconds)
