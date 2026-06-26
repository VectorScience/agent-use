#!/usr/bin/env python3
"""
定时识别 Cursor Agent 面板并点击「发送」按钮。

支持三种识别方式：
  - cdp  : 通过 Chrome DevTools Protocol（推荐，需用调试端口启动 Cursor）
  - uia  : 通过 Windows UI Automation（无需特殊启动参数，但稳定性略差）
  - auto : 先尝试 cdp，失败则回退 uia

CDP 不会新建 Cursor 窗口，而是连接【已打开】的实例。
前提：用调试端口启动 Cursor（若已在运行，需重启一次，工作区/Agent 会话通常会保留）：
  "%LOCALAPPDATA%\\Programs\\cursor\\Cursor.exe" --remote-debugging-port=9222

脚本会操作当前窗口里【已选中】的 Agent 对话，不会切换聊天标签。
多窗口时用 --list-windows 查看，--window-title 指定目标窗口。

完整 GUI 控制（手机/网页）请使用同目录的 FastAPI 服务：
  uv run python -m server.main
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import cursor_cdp as cdp

SEND_BUTTON_NAMES = ("Send", "发送", "Submit", "提交")

LOG = logging.getLogger("click_send")


# --------------------------------------------------------------------------- #
# UI Automation fallback (kept here — only relevant to this CLI)
# --------------------------------------------------------------------------- #
def find_cursor_window_uia():
    import uiautomation as auto

    win = auto.WindowControl(searchDepth=1, RegexName=".*Cursor.*")
    if win.Exists(1, 0):
        return win
    return None


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


# --------------------------------------------------------------------------- #
# Unified runner
# --------------------------------------------------------------------------- #
def run_cdp_once(cdp_base: str, *, window_title: str | None, prefer_foreground: bool) -> dict:
    target = cdp.pick_workbench_target(
        cdp_base, window_title=window_title, prefer_foreground=prefer_foreground
    )
    if not target:
        return {"ok": False, "reason": "no Cursor workbench target found"}
    if not cdp.agent_panel_visible_by_ws(target.ws_url):
        return {"ok": False, "reason": "agent/composer panel not detected in DOM"}
    result = cdp.click_send(cdp_base, window_title=window_title)
    if result.get("ok"):
        result["method"] = "cdp"
    return result


def probe_once(
    method: str,
    cdp_base: str,
    *,
    window_title: str | None,
    prefer_foreground: bool,
) -> dict:
    """检测 Agent 面板与发送按钮是否可达，不点击。"""
    if method == "cdp":
        try:
            status = cdp.read_status(cdp_base, window_title=window_title)
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
            return {"ok": True, "method": "uia", "button": btn.Name or "(icon)"}
        return {"ok": False, "reason": "send button not found or disabled"}

    cdp_result = probe_once(
        "cdp", cdp_base, window_title=window_title, prefer_foreground=prefer_foreground
    )
    if cdp_result.get("ok"):
        return cdp_result
    uia_result = probe_once("uia", cdp_base, window_title=window_title, prefer_foreground=prefer_foreground)
    if uia_result.get("ok"):
        return uia_result
    return {"ok": False, "reason": f"cdp: {cdp_result.get('reason')}; uia: {uia_result.get('reason')}"}


def run_once(
    method: str,
    cdp_base: str,
    *,
    window_title: str | None,
    prefer_foreground: bool,
) -> dict:
    cdp_kwargs = {"window_title": window_title, "prefer_foreground": prefer_foreground}
    if method == "cdp":
        return run_cdp_once(cdp_base, **cdp_kwargs)
    if method == "uia":
        return run_uia_once()

    cdp_result = run_cdp_once(cdp_base, **cdp_kwargs)
    if cdp_result.get("ok"):
        return cdp_result
    uia_result = run_uia_once()
    if uia_result.get("ok"):
        return uia_result
    return {"ok": False, "reason": f"cdp: {cdp_result.get('reason')}; uia: {uia_result.get('reason')}"}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="定时点击 Cursor Agent 面板的发送按钮")
    parser.add_argument("--interval", type=float, default=5.0, help="点击间隔（秒），默认 5")
    parser.add_argument(
        "--method", choices=("auto", "cdp", "uia"), default="cdp", help="识别方式，默认 cdp"
    )
    parser.add_argument("--cdp-port", type=int, default=9222, help="CDP 调试端口，默认 9222")
    parser.add_argument("--max-clicks", type=int, default=0, help="最多点击次数，0 表示无限循环")
    parser.add_argument("--dry-run", action="store_true", help="只检测面板和按钮，不实际点击")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出（适合测试）")
    parser.add_argument(
        "--window-title", default=None, help="按窗口标题子串匹配 Cursor 窗口（多窗口时使用）"
    )
    parser.add_argument(
        "--no-prefer-foreground", action="store_true", help="不优先使用当前前台 Cursor 窗口"
    )
    parser.add_argument("--list-windows", action="store_true", help="列出 CDP 可见的 Cursor 窗口后退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cdp_base = f"http://127.0.0.1:{args.cdp_port}"
    prefer_foreground = not args.no_prefer_foreground

    if args.list_windows:
        windows = cdp.list_windows(cdp_base)
        if not windows:
            cdp.log_cdp_connection_help(args.cdp_port)
            return 1
        for i, w in enumerate(windows, 1):
            marker = " <-- foreground" if w.get("foreground") else ""
            LOG.info("[%s] %s%s", i, w["title"], marker)
        return 0

    LOG.info(
        "started method=%s interval=%.1fs cdp=%s dry_run=%s",
        args.method, args.interval, cdp_base, args.dry_run,
    )

    if args.method in ("auto", "cdp"):
        LOG.info(
            "CDP 连接已有 Cursor 窗口（非新建）。需用调试端口启动：Cursor.exe --remote-debugging-port=%s",
            args.cdp_port,
        )

    cdp_kwargs = {"window_title": args.window_title, "prefer_foreground": prefer_foreground}
    clicks = 0

    try:
        while True:
            if args.dry_run:
                result = probe_once(args.method, cdp_base, **cdp_kwargs)
                if result.get("ok"):
                    LOG.info("probe ok via %s", result.get("method"))
                else:
                    LOG.info("probe failed: %s", result.get("reason"))
            else:
                result = run_once(args.method, cdp_base, **cdp_kwargs)
                if result.get("ok"):
                    clicks += 1
                    LOG.info("clicked #%s via %s", clicks, result.get("method", args.method))
                    if args.max_clicks and clicks >= args.max_clicks:
                        LOG.info("reached max clicks=%s, exit", args.max_clicks)
                        return 0
                else:
                    LOG.debug("skip: %s", result.get("reason"))

            if args.once:
                return 0
            time.sleep(max(args.interval, 0.2))
    except KeyboardInterrupt:
        LOG.info("stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
