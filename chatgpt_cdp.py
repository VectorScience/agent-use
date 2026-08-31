"""ChatGPT (Codex) 桌面应用 CDP profile。

ChatGPT 桌面版与 Cursor 的 composer 同构（TipTap ProseMirror + aria-label
按钮），差异只在：页面 URL 是 ``app://``、发送/停止按钮的标签、以及没有
模式/模型下拉。这里只声明差异，全部行为复用 ``cursor_cdp`` 的实现。

启动要求（见 start_chatgpt_cdp.ps1）::

    ChatGPT.exe --remote-debugging-port=9223 --remote-allow-origins=*
"""

from __future__ import annotations

import subprocess
from typing import Iterable

import cursor_cdp as base
from cursor_cdp import CdpProfile

APP_NAME = "chatgpt"
APP_URL_PREFIX = "app://"

# 主窗口以外的 app:// page（如头像浮层）
_OVERLAY_MARKERS = ("avatar-overlay",)


def match_chatgpt_pages(targets: Iterable[dict]) -> list[dict]:
    """从 CDP targets 里挑出 ChatGPT 主窗口页面。"""
    return [
        t for t in targets
        if t.get("type") == "page"
        and (t.get("url") or "").startswith(APP_URL_PREFIX)
        and not any(m in (t.get("url") or "") for m in _OVERLAY_MARKERS)
    ]


def classify_chatgpt_title(title: str) -> str:
    return APP_NAME


def is_chatgpt_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ChatGPT.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return "ChatGPT.exe" in (result.stdout or "")
    except Exception:
        return False


CHATGPT_PROFILE = CdpProfile(
    name=APP_NAME,
    send_selectors=[
        'button[aria-label="发送"]',
        'button[aria-label="Send"]',
    ],
    stop_selectors=[
        'button[aria-label="停止"]',
        'button[aria-label="Stop"]',
    ],
    composer_selectors=[".ProseMirror[contenteditable='true']"],
    composer_input_selectors=[".ProseMirror[contenteditable='true']"],
    # ChatGPT 没有等价控件，保持为空即可让相关操作安全地返回 not-found
    chat_tab_selectors=[],
    mode_dropdown_selectors=[],
    model_dropdown_selectors=[],
    mode_menu_item_selectors=[],
    match_pages=match_chatgpt_pages,
    classify_title=classify_chatgpt_title,
    is_running=is_chatgpt_running,
)


def log_chatgpt_connection_help(port: int) -> None:
    base.LOG.error("无法连接 ChatGPT CDP (http://127.0.0.1:%s/json)", port)
    if is_chatgpt_running():
        base.LOG.error(
            "ChatGPT 已在运行但调试端口未开启。"
            "解决：完全退出 ChatGPT 后运行 start_chatgpt_cdp.ps1 重启。"
        )
    else:
        base.LOG.error("未检测到 ChatGPT 进程，请先运行 start_chatgpt_cdp.ps1。")
