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
    # ChatGPT 专属：目标条操作（发送前恢复/清除目标）
    extra_actions=("goal_resume", "goal_clear", "goal_edit"),
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


def run_extra_action(cdp_base: str, action: str, *, window_title: str | None = None) -> dict:
    """执行 ChatGPT 专属操作（由 profile.extra_actions 声明的能力）。

    action: ``goal_resume`` / ``goal_clear`` / ``goal_edit``
    """
    mapping = {
        "goal_resume": "resume",
        "goal_clear": "clear",
        "goal_edit": "edit",
    }
    short = mapping.get(action)
    if not short:
        return {"ok": False, "reason": f"未知操作: {action!r} (可选: {', '.join(mapping)})"}
    return click_goal_action(cdp_base, short, window_title=window_title)


def goal_status(cdp_base: str, *, window_title: str | None = None) -> dict:
    """读取目标条状态（是否受限、可用操作）。"""
    return read_goal_status(cdp_base, window_title=window_title)


# --------------------------------------------------------------------------- #
# ChatGPT 专属：目标（goal）操作
# --------------------------------------------------------------------------- #
GOAL_ACTION_SELECTORS = {
    "resume": 'button[aria-label="恢复目标"]',
    "clear": 'button[aria-label="清除目标"]',
    "edit": 'button[aria-label="编辑目标"]',
}


def click_goal_action(
    cdp_base: str,
    action: str,
    *,
    window_title: str | None = None,
) -> dict:
    """点击目标条上的操作按钮。``action``: resume | clear | edit。"""
    import json

    selector = GOAL_ACTION_SELECTORS.get(action)
    if not selector:
        return {"ok": False, "reason": f"未知 goal action: {action!r}（可选 {list(GOAL_ACTION_SELECTORS)}）"}

    tgt = base.pick_workbench_target(cdp_base, window_title=window_title, profile=CHATGPT_PROFILE)
    if not tgt:
        return {"ok": False, "reason": "未找到 ChatGPT 主窗口（app:// 页面）"}

    expr = f"""
(() => {{
  const btn = document.querySelector({json.dumps(selector)});
  if (!btn) return {{ ok: false, reason: 'goal button not found' }};
  if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return {{ ok: false, reason: 'disabled' }};
  const r = btn.getBoundingClientRect();
  if (r.width <= 0) return {{ ok: false, reason: 'not visible' }};
  btn.scrollIntoView({{ block: 'nearest' }});
  btn.click();
  return {{ ok: true, action: {json.dumps(action)} }};
}})()
"""
    result = base._cdp_evaluate(tgt.ws_url, expr)
    return result if isinstance(result, dict) else {"ok": False, "reason": "unexpected evaluate result"}


def read_goal_status(
    cdp_base: str,
    *,
    window_title: str | None = None,
) -> dict:
    """读取目标条状态：受限/受限提示、可见的 goal 按钮。"""
    import json

    tgt = base.pick_workbench_target(cdp_base, window_title=window_title, profile=CHATGPT_PROFILE)
    if not tgt:
        return {"ok": False, "reason": "未找到 ChatGPT 主窗口"}

    expr = f"""
(() => {{
  const actions = {json.dumps(GOAL_ACTION_SELECTORS)};
  const buttons = {{}};
  for (const [name, sel] of Object.entries(actions)) {{
    const b = document.querySelector(sel);
    if (!b) {{ buttons[name] = false; continue; }}
    const r = b.getBoundingClientRect();
    buttons[name] = r.width > 0;
  }}
  // 目标条文本：含操作按钮的最近容器
  let barText = '';
  const anyBtn = document.querySelector(Object.values(actions).join(', '));
  if (anyBtn) {{
    let bar = anyBtn;
    for (let i = 0; i < 6 && bar.parentElement; i++) {{
      bar = bar.parentElement;
      const txt = (bar.textContent || '').trim();
      if (txt.length > 8 && !bar.querySelector('.ProseMirror')) {{ barText = txt.slice(0, 120); break; }}
    }}
  }}
  return {{ ok: true, buttons, bar_text: barText, restricted: buttons.resume === true }};
}})()
"""
    result = base._cdp_evaluate(tgt.ws_url, expr)
    return result if isinstance(result, dict) else {"ok": False, "reason": "unexpected evaluate result"}
