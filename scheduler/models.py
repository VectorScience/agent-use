from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

MESSAGE_SEPARATOR = "\n---\n"

DEFAULT_COMPOSER_MODE = "Agent"
DEFAULT_MODEL = "GLM-5.2"


def effective_composer_mode(value: str | None) -> str:
    s = (value or "").strip()
    return s if s else DEFAULT_COMPOSER_MODE


def effective_model(value: str | None) -> str:
    s = (value or "").strip()
    return s if s else DEFAULT_MODEL


def parse_messages(text: str) -> list[str]:
    """将文案按 ``---`` 分隔为多条命令。"""
    parts = [p.strip() for p in text.split(MESSAGE_SEPARATOR)]
    return [p for p in parts if p]


@dataclass
class ScheduledTask:
    """一条定时发送任务。"""

    id: str
    name: str
    time: str  # HH:MM，每天触发
    message: str  # 单条或含 --- 分隔的多条
    app: str = "cursor"  # cursor | chatgpt
    window_title: str | None = None
    window_mode: str = "auto"  # legacy | agents | auto
    chat_tab: str | None = None
    composer_mode: str | None = None  # Agent / Ask / Edit ...
    model: str | None = None  # auto / GLM-5.2 ...
    wait_between: bool = True
    wait_timeout_seconds: int = 3600
    enabled: bool = True
    no_click: bool = False
    goal_action: str | None = None  # 任务类型: None=发送消息 | resume/clear/edit=目标操作（不发文案）
    last_run_at: str | None = None
    last_result: str | None = None

    def messages_list(self) -> list[str]:
        parsed = parse_messages(self.message)
        return parsed if parsed else ([self.message] if self.message.strip() else [])

    @staticmethod
    def new(
        name: str,
        time: str,
        message: str,
        *,
        app: str = "cursor",
        window_title: str | None = None,
        window_mode: str = "auto",
        chat_tab: str | None = None,
        composer_mode: str | None = None,
        model: str | None = None,
        wait_between: bool = True,
        wait_timeout_seconds: int = 3600,
        enabled: bool = True,
        no_click: bool = False,
        goal_action: str | None = None,
    ) -> ScheduledTask:
        return ScheduledTask(
            id=uuid.uuid4().hex[:12],
            name=name,
            time=time,
            message=message,
            app=app,
            window_title=window_title,
            window_mode=window_mode,
            chat_tab=chat_tab,
            composer_mode=composer_mode,
            model=model,
            wait_between=wait_between,
            wait_timeout_seconds=wait_timeout_seconds,
            enabled=enabled,
            no_click=no_click,
            goal_action=goal_action,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["message_count"] = len(self.messages_list())
        d["composer_mode_effective"] = effective_composer_mode(self.composer_mode)
        d["model_effective"] = effective_model(self.model)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledTask:
        # 兼容历史字段 resume_goal: bool -> goal_action
        goal_action = data.get("goal_action")
        if not goal_action and data.get("resume_goal"):
            goal_action = "resume"
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            time=str(data["time"]),
            message=str(data.get("message") or ""),
            app=str(data.get("app") or "cursor"),
            window_title=data.get("window_title"),
            window_mode=str(data.get("window_mode") or "auto"),
            chat_tab=data.get("chat_tab"),
            composer_mode=data.get("composer_mode"),
            model=data.get("model"),
            wait_between=bool(data.get("wait_between", True)),
            wait_timeout_seconds=int(data.get("wait_timeout_seconds") or 3600),
            enabled=bool(data.get("enabled", True)),
            no_click=bool(data.get("no_click", False)),
            goal_action=goal_action,
            last_run_at=data.get("last_run_at"),
            last_result=data.get("last_result"),
        )
