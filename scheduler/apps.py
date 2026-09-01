"""受控应用注册表：app 名 → CdpProfile + CDP 端口。

调度器 / GUI / CLI 都从这里解析目标应用，避免端口和 profile 的映射
散落在多处（DRY）。新增受控应用只需在这里注册一行。
"""

from __future__ import annotations

import cursor_cdp
from cursor_cdp import CdpProfile, CURSOR_PROFILE

APP_CURSOR = "cursor"
APP_CHATGPT = "chatgpt"

# 各应用的默认 CDP 调试端口（与 start_*.ps1 保持一致）
DEFAULT_CDP_PORTS: dict[str, int] = {
    APP_CURSOR: 9222,
    APP_CHATGPT: 9223,
}


def _load_chatgpt_profile() -> CdpProfile:
    """惰性导入，避免 scheduler 导入链强依赖 chatgpt_cdp。"""
    import chatgpt_cdp

    return chatgpt_cdp.CHATGPT_PROFILE


def get_profile(app: str | None) -> CdpProfile:
    """按应用名解析 profile；未知名称回退 Cursor（fail-fast 交给调用方日志）。"""
    name = (app or APP_CURSOR).strip().lower()
    if name == APP_CURSOR:
        return CURSOR_PROFILE
    if name == APP_CHATGPT:
        return _load_chatgpt_profile()
    raise ValueError(f"未知应用: {app!r}（可选: {', '.join(DEFAULT_CDP_PORTS)}）")


def get_cdp_base(app: str | None, cdp_port: int | None = None) -> str:
    """按应用名解析 CDP 地址；显式端口优先于应用默认端口。"""
    name = (app or APP_CURSOR).strip().lower()
    port = cdp_port or DEFAULT_CDP_PORTS.get(name, DEFAULT_CDP_PORTS[APP_CURSOR])
    return f"http://127.0.0.1:{port}"


def supported_apps() -> list[str]:
    return list(DEFAULT_CDP_PORTS)


def run_extra_action(app: str | None, action: str, *, window_title: str | None = None) -> dict:
    """执行应用专属操作（如 ChatGPT 的 goal_resume/goal_clear/goal_edit）。

    应用未声明该能力时立即报错（fail-fast），避免静默失败。
    """
    profile = get_profile(app)
    if action not in profile.extra_actions:
        raise ValueError(
            f"应用 {profile.name!r} 不支持操作 {action!r} "
            f"(支持: {', '.join(profile.extra_actions) or '无'})"
        )

    if profile.name == APP_CHATGPT:
        import chatgpt_cdp

        short = action.removeprefix("goal_")  # goal_resume -> resume
        return chatgpt_cdp.click_goal_action(
            get_cdp_base(app), short, window_title=window_title
        )

    return {"ok": False, "reason": f"应用 {profile.name} 未实现 {action}"}


def read_extra_status(app: str | None, *, window_title: str | None = None) -> dict:
    """读取应用专属状态（如 ChatGPT 目标条）。无专属状态的应用返回空。"""
    profile = get_profile(app)
    if profile.name == APP_CHATGPT:
        import chatgpt_cdp

        return chatgpt_cdp.read_goal_status(get_cdp_base(app), window_title=window_title)
    return {"ok": True, "supported": False}


__all__ = [
    "APP_CURSOR",
    "APP_CHATGPT",
    "DEFAULT_CDP_PORTS",
    "get_profile",
    "get_cdp_base",
    "supported_apps",
    "run_extra_action",
    "read_extra_status",
]
