#!/usr/bin/env python3
"""
控制 Cursor Agent 面板：定时点击发送、写入文案后发送、定时调度。

典型用法：
  uv run python click_send.py --interval 5
  uv run python click_send.py --send-once --window-mode legacy --window-title PaperHub -m "继续 Phase 2"
  uv run python click_send.py --send-once --window-mode agents -m "继续 Phase 2"
  uv run python click_send.py --at 06:30 --window-mode legacy --window-title PaperHub -m "..."
  uv run python click_send.py --list-windows

GUI 定时任务: run_scheduler_gui.bat 或 uv run python -m scheduler.app
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time

import cursor_cdp as cdp
from scheduler.apps import get_cdp_base, get_profile
from scheduler.models import effective_composer_mode, effective_model, parse_messages
from scheduler.send import send_message, send_message_sequence

SEND_BUTTON_NAMES = ("Send", "发送", "Submit", "提交")
LOG = logging.getLogger("click_send")


def wait_until(target: dt.datetime, *, poll_seconds: float = 5.0) -> None:
    LOG.info("计划触发时刻: %s", target.strftime("%Y-%m-%d %H:%M:%S"))
    last_log_minute = -1
    while True:
        now = dt.datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        minute_left = int(remaining // 60)
        if minute_left != last_log_minute:
            LOG.info("距触发还有 %s 分 %s 秒", minute_left, int(remaining % 60))
            last_log_minute = minute_left
        time.sleep(min(poll_seconds, max(remaining, 0.5)))


def parse_at(value: str) -> dt.datetime:
    value = value.strip()
    now = dt.datetime.now()
    if value.startswith("+"):
        body = value[1:].lower()
        num = int(body[:-1])
        if body.endswith("h"):
            return now + dt.timedelta(hours=num)
        if body.endswith("m"):
            return now + dt.timedelta(minutes=num)
        if body.endswith("s"):
            return now + dt.timedelta(seconds=num)
        raise ValueError(f"相对时间需以 h/m/s 结尾: {value!r}")
    try:
        time_part = dt.datetime.strptime(value, "%H:%M").time()
        candidate = dt.datetime.combine(now.date(), time_part)
        if candidate <= now:
            candidate += dt.timedelta(days=1)
        return candidate
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {value!r}")


# UIA fallback (unchanged)
def find_cursor_window_uia():
    import uiautomation as auto
    win = auto.WindowControl(searchDepth=1, RegexName=".*Cursor.*")
    return win if win.Exists(1, 0) else None


def uia_find_send_button(window):
    import uiautomation as auto
    for name in SEND_BUTTON_NAMES:
        btn = window.ButtonControl(Name=name, searchDepth=12)
        if btn.Exists(0.3, 0):
            return btn
    return None


def uia_agent_panel_visible(window) -> bool:
    if window.EditControl(searchDepth=12).Exists(0.3, 0):
        return True
    if window.DocumentControl(searchDepth=12).Exists(0.3, 0):
        return True
    for hint in ("Agent", "Chat", "Composer", "agent", "chat"):
        if window.TextControl(SubName=hint, searchDepth=8).Exists(0.2, 0):
            return True
    return False


def run_uia_once() -> dict:
    window = find_cursor_window_uia()
    if not window:
        return {"ok": False, "reason": "Cursor window not found"}
    if not uia_agent_panel_visible(window):
        return {"ok": False, "reason": "agent panel not detected via UI Automation"}
    btn = uia_find_send_button(window)
    if not btn:
        return {"ok": False, "reason": "send button not found"}
    if not btn.IsEnabled:
        return {"ok": False, "reason": "send button disabled"}
    try:
        btn.Click(simulateMove=False)
        return {"ok": True, "method": "uia", "button": btn.Name or "(icon)"}
    except Exception as exc:
        return {"ok": False, "reason": f"click failed: {exc}"}


def run_cdp_once(cdp_base: str, *, window_title: str | None, window_mode: str, prefer_foreground: bool) -> dict:
    target = cdp.pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, prefer_foreground=prefer_foreground
    )
    if not target:
        return {"ok": False, "reason": "no Cursor workbench target found"}
    if not cdp.agent_panel_visible_by_ws(target.ws_url):
        return {"ok": False, "reason": "agent/composer panel not detected in DOM"}
    result = cdp.click_send(cdp_base, window_title=window_title, window_mode=window_mode)
    if result.get("ok"):
        result["method"] = "cdp"
    return result


def probe_once(method: str, cdp_base: str, *, window_title: str | None, window_mode: str, prefer_foreground: bool) -> dict:
    if method == "cdp":
        try:
            status = cdp.read_status(cdp_base, window_title=window_title, window_mode=window_mode)
            if not status.cdp_reachable:
                return {"ok": False, "reason": "CDP unreachable"}
            if not status.agent_panel_visible:
                return {"ok": False, "reason": "agent panel not visible"}
            if status.send_button_enabled:
                return {"ok": True, "method": "cdp"}
            return {"ok": False, "reason": "send button not found or disabled"}
        except Exception as exc:
            return {"ok": False, "reason": f"cdp probe failed: {exc}"}
    if method == "uia":
        window = find_cursor_window_uia()
        if not window:
            return {"ok": False, "reason": "Cursor window not found"}
        if not uia_agent_panel_visible(window):
            return {"ok": False, "reason": "agent panel not visible (uia)"}
        btn = uia_find_send_button(window)
        if btn and btn.IsEnabled:
            return {"ok": True, "method": "uia"}
        return {"ok": False, "reason": "send button not found or disabled"}
    cdp_result = probe_once("cdp", cdp_base, window_title=window_title, window_mode=window_mode, prefer_foreground=prefer_foreground)
    if cdp_result.get("ok"):
        return cdp_result
    return probe_once("uia", cdp_base, window_title=window_title, window_mode=window_mode, prefer_foreground=prefer_foreground)


def run_once(method: str, cdp_base: str, *, window_title: str | None, window_mode: str, prefer_foreground: bool) -> dict:
    if method == "cdp":
        return run_cdp_once(cdp_base, window_title=window_title, window_mode=window_mode, prefer_foreground=prefer_foreground)
    if method == "uia":
        return run_uia_once()
    cdp_result = run_cdp_once(cdp_base, window_title=window_title, window_mode=window_mode, prefer_foreground=prefer_foreground)
    if cdp_result.get("ok"):
        return cdp_result
    return run_uia_once()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="控制 Cursor Agent：定时点击 / 写文案发送 / 定时调度")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--method", choices=("auto", "cdp", "uia"), default="cdp")
    parser.add_argument(
        "--app",
        choices=("cursor", "chatgpt"),
        default="cursor",
        help="目标应用: cursor (CDP 9222) / chatgpt (CDP 9223)",
    )
    parser.add_argument("--cdp-port", type=int, default=None, help="覆盖应用默认 CDP 端口")
    parser.add_argument("--max-clicks", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--message", "-m", default=None)
    parser.add_argument("--message-file", default=None)
    parser.add_argument("--send-once", action="store_true")
    parser.add_argument("--no-click", action="store_true")
    parser.add_argument("--at", default=None)
    parser.add_argument("--window-title", default=None)
    parser.add_argument(
        "--window-mode",
        choices=("auto", "legacy", "agents"),
        default="auto",
        help="窗口模式: legacy=项目内旧Agent, agents=新版Cursor Agents",
    )
    parser.add_argument("--chat-tab", default=None, help="切换到指定 Agent 对话标签（标题子串）")
    parser.add_argument("--composer-mode", default=None, help="Composer 模式，默认 Agent")
    parser.add_argument("--model", default=None, help="模型，默认 GLM-5.2")
    parser.add_argument(
        "--wait-between", action="store_true", default=None,
        help="多条命令时等 Agent 完成再发下一条（默认开启）",
    )
    parser.add_argument("--no-wait-between", action="store_true", help="多条命令时不等待 Agent 完成")
    parser.add_argument("--wait-timeout", type=int, default=3600, help="等待 Agent 完成的最长时间（秒）")
    parser.add_argument("--list-chat-tabs", action="store_true", help="列出 Agent 对话标签")
    parser.add_argument("--no-prefer-foreground", action="store_true")
    parser.add_argument("--list-windows", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def resolve_messages(args: argparse.Namespace) -> list[str]:
    raw = resolve_message_text(args)
    if raw is None:
        return []
    return parse_messages(raw) if raw else []


def resolve_message_text(args: argparse.Namespace) -> str | None:
    if args.message is not None and args.message_file is not None:
        raise SystemExit("[错误] --message 和 --message-file 不能同时使用")
    if args.message is not None:
        return args.message
    if args.message_file:
        with open(args.message_file, encoding="utf-8") as f:
            return f.read()
    return None


def chatgpt_cdp_log_help() -> None:
    try:
        import chatgpt_cdp

        chatgpt_cdp.log_chatgpt_connection_help(9223)
    except Exception:
        cdp.LOG.error("ChatGPT CDP 不可用，请运行 start_chatgpt_cdp.ps1")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    cdp_base = get_cdp_base(args.app, args.cdp_port)
    profile = get_profile(args.app)
    prefer_foreground = not args.no_prefer_foreground
    send_kwargs = {
        "window_title": args.window_title,
        "window_mode": args.window_mode,
        "prefer_foreground": prefer_foreground,
        "chat_tab": args.chat_tab,
        "composer_mode": effective_composer_mode(args.composer_mode),
        "model": effective_model(args.model),
        "profile": profile,
    }
    wait_between = True
    if args.no_wait_between:
        wait_between = False
    elif args.wait_between is True:
        wait_between = True

    if args.list_chat_tabs:
        tabs = cdp.list_chat_tabs(
            cdp_base,
            window_title=args.window_title,
            window_mode=args.window_mode,
            profile=profile,
        )
        if not tabs:
            LOG.info("no chat tabs found")
            return 1
        for i, t in enumerate(tabs, 1):
            mark = " <-- active" if t.get("selected") else ""
            LOG.info("[%s] %s%s", i, t.get("title"), mark)
        return 0

    if args.list_windows:
        windows = cdp.list_windows(cdp_base, profile=profile)
        if not windows:
            if args.app == "chatgpt":
                chatgpt_cdp_log_help()
            else:
                cdp.log_cdp_connection_help(args.cdp_port or 9222)
            return 1
        for i, w in enumerate(windows, 1):
            fg = " <-- foreground" if w.get("foreground") else ""
            mode = w.get("mode", "?")
            LOG.info("[%s] [%s] %s%s", i, mode, w["title"], fg)
        return 0

    def do_send(messages: list[str]) -> dict:
        common = {**send_kwargs, "no_click": args.no_click, "wait_timeout_seconds": args.wait_timeout}
        if len(messages) <= 1:
            return send_message(cdp_base, messages[0], **common)
        return send_message_sequence(
            cdp_base, messages, wait_between=wait_between, **common
        )

    if args.at:
        messages = resolve_messages(args)
        if not messages:
            LOG.error("--at 需要 --message 或 --message-file")
            return 1
        target_time = parse_at(args.at)
        LOG.info("排定 %s | %d 条命令", target_time, len(messages))
        wait_until(target_time)
        result = do_send(messages)
        if result.get("ok"):
            LOG.info("发送成功 (%s 条)", result.get("count", len(messages)))
            return 0
        LOG.error("发送失败: %s", result.get("reason"))
        return 1

    if args.send_once:
        messages = resolve_messages(args)
        if not messages:
            LOG.error("--send-once 需要 --message 或 --message-file")
            return 1
        result = do_send(messages)
        if result.get("ok"):
            LOG.info("发送成功 (%s 条)", result.get("count", len(messages)))
            return 0
        LOG.error("发送失败: %s", result.get("reason"))
        return 1

    LOG.info("loop mode interval=%.1fs", args.interval)
    clicks = 0
    try:
        while True:
            if args.dry_run:
                result = probe_once(
                    args.method, cdp_base,
                    window_title=args.window_title,
                    window_mode=args.window_mode,
                    prefer_foreground=prefer_foreground,
                )
                LOG.info("probe: %s", result.get("ok") or result.get("reason"))
            else:
                result = run_once(
                    args.method, cdp_base,
                    window_title=args.window_title,
                    window_mode=args.window_mode,
                    prefer_foreground=prefer_foreground,
                )
                if result.get("ok"):
                    clicks += 1
                    LOG.info("clicked #%s", clicks)
                    if args.max_clicks and clicks >= args.max_clicks:
                        return 0
            if args.once:
                return 0
            time.sleep(max(args.interval, 0.2))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
