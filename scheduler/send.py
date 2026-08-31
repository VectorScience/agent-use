"""写入文案并发送 — CLI / 调度器 / GUI 共用。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import cursor_cdp as cdp
from cursor_cdp import CdpProfile, CURSOR_PROFILE
from scheduler.models import effective_composer_mode, effective_model

LOG = logging.getLogger("scheduler.send")


@dataclass
class SendOptions:
    window_title: str | None = None
    window_mode: str = cdp.WINDOW_MODE_AUTO
    prefer_foreground: bool = True
    chat_tab: str | None = None
    composer_mode: str | None = None
    model: str | None = None
    no_click: bool = False
    max_wait_seconds: int = 30
    wait_between: bool = False
    wait_timeout_seconds: int = 3600
    profile: CdpProfile = CURSOR_PROFILE


def apply_composer_settings(cdp_base: str, opts: SendOptions) -> list[dict]:
    """切换对话标签、模式、模型。返回各步骤结果（失败不中断，记录在日志）。"""
    pick = {
        "window_title": opts.window_title,
        "window_mode": opts.window_mode,
        "profile": opts.profile,
    }
    steps: list[dict] = []

    # ChatGPT 等无模式下拉的应用：选择器为空，直接跳过对应步骤
    can_switch_tab = bool(opts.profile.chat_tab_selectors)
    can_set_mode = bool(opts.profile.mode_dropdown_selectors)
    can_set_model = bool(opts.profile.model_dropdown_selectors)

    if opts.chat_tab and can_switch_tab:
        r = cdp.switch_chat_tab(cdp_base, opts.chat_tab, **pick)
        steps.append({"step": "chat_tab", **r})
        if r.get("ok"):
            LOG.info("已切换对话: %s", r.get("title"))
            time.sleep(0.4)
        else:
            LOG.warning("切换对话失败: %s", r.get("reason"))

    if can_set_mode:
        mode = effective_composer_mode(opts.composer_mode)
        r = cdp.set_composer_mode(cdp_base, mode, **pick)
        steps.append({"step": "composer_mode", **r})
        if r.get("ok"):
            LOG.info("已设置模式: %s", r.get("mode"))
            time.sleep(0.4)
        else:
            LOG.warning("设置模式失败: %s", r.get("reason"))

    if can_set_model:
        r = cdp.set_composer_model(cdp_base, effective_model(opts.model), **pick)
        steps.append({"step": "model", **r})
        if r.get("ok"):
            LOG.info("已设置模型: %s", r.get("model"))
            time.sleep(0.4)
        else:
            LOG.warning("设置模型失败: %s", r.get("reason"))

    return steps


def send_message(
    cdp_base: str,
    message: str,
    *,
    window_title: str | None = None,
    window_mode: str = cdp.WINDOW_MODE_AUTO,
    prefer_foreground: bool = True,
    chat_tab: str | None = None,
    composer_mode: str | None = None,
    model: str | None = None,
    no_click: bool = False,
    max_wait_seconds: int = 30,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    opts = SendOptions(
        window_title=window_title,
        window_mode=window_mode,
        prefer_foreground=prefer_foreground,
        chat_tab=chat_tab,
        composer_mode=composer_mode,
        model=model,
        no_click=no_click,
        max_wait_seconds=max_wait_seconds,
        profile=profile,
    )
    return _send_one(cdp_base, message, opts)


def send_message_sequence(
    cdp_base: str,
    messages: list[str],
    *,
    window_title: str | None = None,
    window_mode: str = cdp.WINDOW_MODE_AUTO,
    prefer_foreground: bool = True,
    chat_tab: str | None = None,
    composer_mode: str | None = None,
    model: str | None = None,
    no_click: bool = False,
    wait_between: bool = True,
    wait_timeout_seconds: int = 3600,
    max_wait_seconds: int = 30,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """连续发送多条命令；默认等上一条 Agent 跑完再发下一条。"""
    if not messages:
        return {"ok": False, "reason": "empty messages"}

    opts = SendOptions(
        window_title=window_title,
        window_mode=window_mode,
        prefer_foreground=prefer_foreground,
        chat_tab=chat_tab,
        composer_mode=composer_mode,
        model=model,
        no_click=no_click,
        max_wait_seconds=max_wait_seconds,
        wait_between=wait_between,
        wait_timeout_seconds=wait_timeout_seconds,
        profile=profile,
    )

    # 首条前：切换标签 / 模式 / 模型
    setup_steps = apply_composer_settings(cdp_base, opts)

    results: list[dict] = []
    pick = {
        "window_title": opts.window_title,
        "window_mode": opts.window_mode,
        "profile": opts.profile,
    }

    for i, msg in enumerate(messages):
        if i > 0 and opts.wait_between and not opts.no_click:
            LOG.info("等待 Agent 完成第 %s 条…", i)
            idle = cdp.wait_for_agent_idle(
                cdp_base,
                timeout_seconds=opts.wait_timeout_seconds,
                **pick,
            )
            if not idle.get("ok"):
                return {
                    "ok": False,
                    "reason": idle.get("reason"),
                    "results": results,
                    "failed_at": i,
                    "setup_steps": setup_steps,
                }

        LOG.info("发送第 %s/%s 条 (前40字): %s", i + 1, len(messages), msg[:40].replace("\n", " "))
        one = _send_one(cdp_base, msg, opts, skip_setup=True)
        one["index"] = i
        results.append(one)
        if not one.get("ok"):
            return {
                "ok": False,
                "reason": one.get("reason"),
                "results": results,
                "failed_at": i,
                "setup_steps": setup_steps,
            }

    return {
        "ok": True,
        "count": len(messages),
        "results": results,
        "setup_steps": setup_steps,
    }


def _send_one(
    cdp_base: str,
    message: str,
    opts: SendOptions,
    *,
    skip_setup: bool = False,
) -> dict:
    pick_kwargs = {
        "window_title": opts.window_title,
        "window_mode": opts.window_mode,
        "prefer_foreground": opts.prefer_foreground,
        "profile": opts.profile,
    }
    pick_only = {
        "window_title": opts.window_title,
        "window_mode": opts.window_mode,
        "profile": opts.profile,
    }

    if not skip_setup:
        apply_composer_settings(cdp_base, opts)

    deadline = time.monotonic() + opts.max_wait_seconds
    last_reason = "未开始"

    while time.monotonic() < deadline:
        if not cdp.is_cdp_reachable(cdp_base):
            last_reason = "CDP 不可达"
            time.sleep(1.0)
            continue

        tgt = cdp.pick_workbench_target(cdp_base, **pick_kwargs)
        if not tgt:
            last_reason = f"未找到匹配的 {opts.profile.name} 窗口"
            time.sleep(1.0)
            continue

        if not cdp.agent_panel_visible_by_ws(tgt.ws_url, profile=opts.profile):
            last_reason = "Agent/composer 面板未打开"
            time.sleep(1.0)
            continue

        status = cdp.read_status(cdp_base, **pick_only)
        if status.stop_button_present:
            last_reason = "Agent 正在运行，等待空闲"
            time.sleep(1.0)
            continue
        break
    else:
        return {"ok": False, "reason": f"等待就绪超时：{last_reason}"}

    tgt = cdp.pick_workbench_target(cdp_base, **pick_kwargs)
    if not tgt:
        return {"ok": False, "reason": f"未找到匹配的 {opts.profile.name} 窗口"}

    mode_label = opts.profile.classify_title(tgt.title)
    LOG.info("目标窗口 [%s]: %s", mode_label, tgt.title or "(无标题)")

    write_result = cdp.set_composer_text(
        cdp_base,
        message,
        window_title=opts.window_title,
        window_mode=opts.window_mode,
        mode="replace",
        profile=opts.profile,
    )
    if not write_result.get("ok"):
        return {"ok": False, "reason": f"写入文案失败：{write_result.get('reason')}"}

    if opts.no_click:
        return {"ok": True, "method": "cdp", "no_click": True, "window_mode": mode_label}

    time.sleep(0.5)
    click_result = cdp.click_send(cdp_base, **pick_only)
    if click_result.get("ok"):
        return {
            "ok": True,
            "method": "cdp",
            "selector": click_result.get("selector"),
            "window_mode": mode_label,
            "app": opts.profile.name,
        }
    return {"ok": False, "reason": f"发送失败：{click_result.get('reason')}"}
