"""Cursor CDP bridge core.

Single source of truth for talking to Cursor's workbench via Chrome DevTools
Protocol. Reused by both ``click_send.py`` (CLI loop) and the FastAPI server.

Design notes
------------
- All public functions are stateless: open WS, do one thing, close WS. This
  keeps the API trivial to reason about and lets callers retry freely.
- Cursors spawned by the same workspace all share one CDP endpoint; we pick a
  target via the foreground window heuristic (matches ``click_send``).
- Screenshots, DOM queries and clicks share the same target selection so the
  user always "sees" and "acts on" the same Cursor window.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable

LOG = logging.getLogger("cursor_cdp")

# Selectors kept in one place so CLI / server / future tests agree.
SEND_BUTTON_SELECTORS: list[str] = [
    # 新版 Cursor (TipTap): ui-prompt-input-submit-button
    ".ui-prompt-input-submit-button",
    'button[aria-label="Send message"]',
    'button[aria-label="发送消息"]',
    # 兼容旧版
    'button[aria-label="Send"]',
    'button[aria-label="发送"]',
    ".send-with-mode .anysphere-icon-button",
    ".send-with-mode button",
]

STOP_BUTTON_SELECTORS: list[str] = [
    'button[aria-label="Stop"]',
    'button[aria-label="停止"]',
    '.stop-button[aria-label="Stop"]',
    '.stop-button',
    '.composer-stop-button',
    '[class*="stop-button"]',
]

COMPOSER_SELECTORS: list[str] = [
    # 新版 Cursor Agents 独立窗口
    ".ui-prompt-input-editor",
    ".ui-prompt-input",
    "[class*='ui-prompt-input']",
    # 旧版项目内 Agent 面板
    "[data-composer-id]",
    ".composer-bar",
    ".composite.auxiliarybar",
    ".aislash-editor-input",
]

# 窗口模式
WINDOW_MODE_LEGACY = "legacy"   # 项目内 Agent，标题如 "file - Project - Cursor"
WINDOW_MODE_AGENTS = "agents"  # 独立 Cursor Agents 窗口，标题如 "Cursor Agents"
WINDOW_MODE_AUTO = "auto"


def classify_window_mode(title: str) -> str:
    """根据窗口标题判断 Agent 面板类型。"""
    t = (title or "").strip().casefold()
    if t == "cursor agents" or t.startswith("cursor agents "):
        return WINDOW_MODE_AGENTS
    if t.endswith(" - cursor") or " - cursor" in t:
        return WINDOW_MODE_LEGACY
    return WINDOW_MODE_LEGACY

# Agent 对话标签（侧栏 / 顶栏）
CHAT_TAB_SELECTORS: list[str] = [
    ".agent-sidebar-cell",
    '[class*="agent-tabs"] li[class*="action-item"] a[aria-id="chat-horizontal-tab"]',
    '[class*="agent-tabs"] li a[aria-id="chat-horizontal-tab"]',
    ".tab .composer-tab-label",
]

MODE_DROPDOWN_SELECTORS: list[str] = [
    ".composer-unified-dropdown",
    '[class*="composer-unified-dropdown"]',
    '[class*="mode-dropdown"]',
]

MODEL_DROPDOWN_SELECTORS: list[str] = [
    ".composer-unified-dropdown-model",
    '[class*="composer-unified-dropdown-model"]',
    '[class*="model-dropdown"]',
]

MODE_MENU_ITEM_SELECTORS: list[str] = [
    ".composer-unified-context-menu-item",
    '[class*="composer-unified-context-menu-item"]',
    '[role="menuitem"]',
    '[role="option"]',
]

COMPOSER_INPUT_SELECTORS: list[str] = [
    # 新版 Cursor 用 TipTap/ProseMirror，类名 ui-prompt-input-editor__input
    ".tiptap.ProseMirror[contenteditable='true']",
    ".ProseMirror[contenteditable='true']",
    ".ui-prompt-input-editor__input[contenteditable='true']",
    # 兼容旧版 Slate
    "[data-composer-id] [contenteditable='true']",
    ".composer-bar [contenteditable='true']",
    ".aislash-editor-input",
]


# --------------------------------------------------------------------------- #
# CDP low-level plumbing
# --------------------------------------------------------------------------- #
def _cdp_send_recv(ws: Any, method: str, params: dict | None, msg_id: int) -> dict:
    """Send one CDP request frame and block until its matching response arrives.

    Events arriving in between are silently dropped — we never subscribe to
    them here, so they'd only arrive if a caller forgot to drain the socket.
    """
    payload = {"id": msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(payload))
    while True:
        raw = ws.recv()
        if not raw:
            raise ConnectionError("CDP WebSocket closed")
        data = json.loads(raw)
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(f"CDP error on {method}: {data['error']}")
            return data.get("result", {})


def _cdp_evaluate(ws_url: str, expression: str, *, await_promise: bool = False) -> Any:
    """Open a short-lived WS to ``ws_url`` and evaluate ``expression``.

    Returns the JS value (``returnByValue``), ``None`` for ``undefined``.
    """
    import websocket

    # 新版 Chromium 要求 Origin 在白名单里。我们主动带一个匹配的 Origin，
    # 配合 Cursor 启动时的 --remote-allow-origins=* 即可握手成功。
    ws = websocket.create_connection(
        ws_url,
        timeout=10,
        origin="http://127.0.0.1:9222",
        suppress_origin=False,
    )
    try:
        _cdp_send_recv(ws, "Runtime.enable", None, 1)
        result = _cdp_send_recv(
            ws,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            2,
        )
        value = result.get("result", {})
        if value.get("type") == "undefined":
            return None
        if "value" in value:
            return value["value"]
        if value.get("subtype") == "error":
            raise RuntimeError(value.get("description", "evaluate failed"))
        return value
    finally:
        ws.close()


# --------------------------------------------------------------------------- #
# Target discovery
# --------------------------------------------------------------------------- #
def list_cdp_targets(cdp_base: str) -> list[dict]:
    url = f"{cdp_base.rstrip('/')}/json"
    with urllib.request.urlopen(url, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_workbench_targets(targets: Iterable[dict]) -> list[dict]:
    return [t for t in targets if t.get("type") == "page" and "workbench" in (t.get("url") or "")]


def get_foreground_window_title() -> str | None:
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        return title or None
    except Exception:
        return None


def is_cdp_reachable(cdp_base: str) -> bool:
    try:
        list_cdp_targets(cdp_base)
        return True
    except Exception:
        return False


def is_cursor_running() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Cursor.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return "Cursor.exe" in (result.stdout or "")
    except Exception:
        return False


def log_cdp_connection_help(port: int) -> None:
    cdp_url = f"http://127.0.0.1:{port}/json"
    LOG.error("无法连接 CDP (%s)", cdp_url)
    if is_cursor_running():
        LOG.error(
            "Cursor 已在运行，但调试端口未开启。"
            "常见原因：带 --remote-debugging-port 启动时，已有实例在后台，参数被忽略。"
        )
        LOG.error("解决：")
        LOG.error("  1. 完全退出 Cursor（托盘 -> Quit / 任务管理器结束所有 Cursor.exe）")
        LOG.error("  2. 运行 start_cursor_cdp.ps1 重新启动")
        LOG.error("  3. 浏览器打开 %s 应看到 JSON 列表", cdp_url)
    else:
        LOG.error("未检测到 Cursor 进程，请先运行 start_cursor_cdp.ps1。")


# --------------------------------------------------------------------------- #
# Application profiles
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CdpProfile:
    """一个受控应用（Cursor / ChatGPT / …）的全部 DOM 约定。

    把「选择器 + 页面识别 + 进程探测」收敛到一处，高层操作函数按 profile
    参数化，避免运行时改共享全局（交错任务时会互相污染）。
    """

    name: str
    send_selectors: list[str]
    stop_selectors: list[str]
    composer_selectors: list[str]
    composer_input_selectors: list[str]
    chat_tab_selectors: list[str]
    mode_dropdown_selectors: list[str]
    model_dropdown_selectors: list[str]
    mode_menu_item_selectors: list[str]
    match_pages: Callable[[Iterable[dict]], list[dict]]
    classify_title: Callable[[str], str] = classify_window_mode
    is_running: Callable[[], bool] = is_cursor_running
    process_name: str = "Cursor.exe"
    # 应用专属能力声明：支持的额外操作名列表（如 ChatGPT 的目标条操作）。
    # 空列表 = 无专属操作；GUI / API 据此显隐对应入口。
    extra_actions: tuple[str, ...] = ()


CURSOR_PROFILE = CdpProfile(
    name="cursor",
    send_selectors=SEND_BUTTON_SELECTORS,
    stop_selectors=STOP_BUTTON_SELECTORS,
    composer_selectors=COMPOSER_SELECTORS,
    composer_input_selectors=COMPOSER_INPUT_SELECTORS,
    chat_tab_selectors=CHAT_TAB_SELECTORS,
    mode_dropdown_selectors=MODE_DROPDOWN_SELECTORS,
    model_dropdown_selectors=MODEL_DROPDOWN_SELECTORS,
    mode_menu_item_selectors=MODE_MENU_ITEM_SELECTORS,
    match_pages=list_workbench_targets,
    classify_title=classify_window_mode,
    is_running=is_cursor_running,
    process_name="Cursor.exe",
)

@dataclass
class WorkbenchTarget:
    ws_url: str
    title: str
    url: str


def pick_workbench_target(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    prefer_foreground: bool = True,
    profile: CdpProfile = CURSOR_PROFILE,
) -> WorkbenchTarget | None:
    """Pick the best workbench page for ``profile``.

    ``window_mode``:
      - ``legacy``: 项目内旧 Agent 窗口（排除 Cursor Agents 独立窗口）
      - ``agents``: 新版 Cursor Agents 独立窗口
      - ``auto``: 不过滤模式

    Selection order:
    1. Filter by ``window_mode`` if not auto.
    2. Filter by ``window_title`` substring if given.
    3. Prefer foreground window.
    4. Prefer Agent panel visible + send button ready.
    """
    try:
        targets = list_cdp_targets(cdp_base)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None

    pages = profile.match_pages(targets)
    if not pages:
        return None

    if window_mode != WINDOW_MODE_AUTO:
        mode_filtered = [
            p for p in pages if profile.classify_title(p.get("title") or "") == window_mode
        ]
        if mode_filtered:
            pages = mode_filtered

    if window_title:
        needle = window_title.casefold()
        pages = [p for p in pages if needle in (p.get("title") or "").casefold()] or pages

    if prefer_foreground and not window_title:
        fg = get_foreground_window_title()
        if fg:
            fg_needle = fg.casefold()
            fg_matched = [p for p in pages if fg_needle in (p.get("title") or "").casefold()]
            pages = fg_matched or pages

    def to_target(p: dict) -> WorkbenchTarget:
        return WorkbenchTarget(
            ws_url=p.get("webSocketDebuggerUrl") or "",
            title=p.get("title") or "",
            url=p.get("url") or "",
        )

    # Pass A: panel visible AND send button ready.
    for page in pages:
        tgt = to_target(page)
        if tgt.ws_url and _target_ready(tgt.ws_url, need_send_button=True, profile=profile):
            return tgt

    # Pass B: panel visible (button not necessarily present yet).
    for page in pages:
        tgt = to_target(page)
        if tgt.ws_url and _target_ready(tgt.ws_url, need_send_button=False, profile=profile):
            return tgt

    # Pass C: anything.
    for page in pages:
        tgt = to_target(page)
        if tgt.ws_url:
            return tgt
    return None


def _target_ready(ws_url: str, need_send_button: bool, *, profile: CdpProfile = CURSOR_PROFILE) -> bool:
    if not agent_panel_visible_by_ws(ws_url, profile=profile):
        return False
    if not need_send_button:
        return True
    selectors_json = json.dumps(profile.send_selectors)
    expr = f"""
(() => {{
  const selectors = {selectors_json};
  for (const sel of selectors) {{
    const btn = document.querySelector(sel);
    if (!btn) continue;
    const disabled = btn.disabled || btn.getAttribute('aria-disabled') === 'true';
    if (disabled) continue;
    const rect = btn.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return true;
  }}
  return false;
}})()
"""
    return bool(_cdp_evaluate(ws_url, expr))


# --------------------------------------------------------------------------- #
# High-level operations
# --------------------------------------------------------------------------- #
def agent_panel_visible_by_ws(ws_url: str, *, profile: CdpProfile = CURSOR_PROFILE) -> bool:
    selectors_json = json.dumps(profile.composer_selectors)
    expr = f"""
(() => {{
  const selectors = {selectors_json};
  return selectors.some(sel => !!document.querySelector(sel));
}})()
"""
    return bool(_cdp_evaluate(ws_url, expr))


@dataclass
class CursorStatus:
    """Snapshot of the workbench relevant to the mobile UI."""

    cdp_reachable: bool
    cursor_running: bool
    agent_panel_visible: bool
    send_button_enabled: bool
    stop_button_present: bool
    workbench_title: str
    composer_text: str
    app: str = "cursor"

    def to_dict(self) -> dict:
        return {
            "app": self.app,
            "cdp_reachable": self.cdp_reachable,
            "cursor_running": self.cursor_running,
            "agent_panel_visible": self.agent_panel_visible,
            "send_button_enabled": self.send_button_enabled,
            "stop_button_present": self.stop_button_present,
            "workbench_title": self.workbench_title,
            "composer_text": self.composer_text,
        }


def read_status(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> CursorStatus:
    """Probe the workbench and return a serialisable status snapshot.

    Each step is defensive — if CDP is unreachable we still return a usable
    status object so the UI can render the "offline" state.
    """
    running = profile.is_running()
    try:
        reachable = is_cdp_reachable(cdp_base)
    except Exception:
        reachable = False

    if not reachable:
        return CursorStatus(
            cdp_reachable=False,
            cursor_running=running,
            agent_panel_visible=False,
            send_button_enabled=False,
            stop_button_present=False,
            workbench_title="",
            composer_text="",
            app=profile.name,
        )

    tgt = pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return CursorStatus(
            cdp_reachable=True,
            cursor_running=running,
            agent_panel_visible=False,
            send_button_enabled=False,
            stop_button_present=False,
            workbench_title="",
            composer_text="",
            app=profile.name,
        )

    expr = f"""
(() => {{
  const sendSel = {json.dumps(profile.send_selectors)};
  const stopSel = {json.dumps(profile.stop_selectors)};
  const inputSel = {json.dumps(profile.composer_input_selectors)};

  let sendEnabled = false;
  for (const sel of sendSel) {{
    const b = document.querySelector(sel);
    if (b && !b.disabled && b.getAttribute('aria-disabled') !== 'true') {{
      const r = b.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {{ sendEnabled = true; break; }}
    }}
  }}

  let stopPresent = false;
  for (const sel of stopSel) {{
    const b = document.querySelector(sel);
    if (b) {{
      const r = b.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {{ stopPresent = true; break; }}
    }}
  }}

  let composerText = '';
  for (const sel of inputSel) {{
    const el = document.querySelector(sel);
    if (el) {{ composerText = (el.innerText || el.textContent || '').trim(); if (composerText) break; }}
  }}

  return {{ sendEnabled, stopPresent, composerText }};
}})()
"""
    try:
        info = _cdp_evaluate(tgt.ws_url, expr) or {}
    except Exception as exc:
        LOG.warning("status evaluate failed: %s", exc)
        info = {}

    return CursorStatus(
        cdp_reachable=True,
        cursor_running=running,
        agent_panel_visible=True,
        send_button_enabled=bool(info.get("sendEnabled")),
        stop_button_present=bool(info.get("stopPresent")),
        workbench_title=tgt.title,
        composer_text=str(info.get("composerText") or ""),
        app=profile.name,
    )


def click_send(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """Find and click the Send button. Returns ``{{ok, selector? | reason}}``."""
    tgt = pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    selectors_json = json.dumps(profile.send_selectors)
    expr = f"""
(() => {{
  const selectors = {selectors_json};
  for (const sel of selectors) {{
    const btn = document.querySelector(sel);
    if (!btn) continue;
    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') continue;
    const rect = btn.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    btn.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});
    btn.click();
    return {{ ok: true, selector: sel }};
  }}
  return {{ ok: false, reason: 'send button not found or disabled' }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    return result


def click_stop(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """Find and click the Stop button (when agent is running)."""
    tgt = pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    selectors_json = json.dumps(profile.stop_selectors)
    expr = f"""
(() => {{
  const selectors = {selectors_json};
  for (const sel of selectors) {{
    const btn = document.querySelector(sel);
    if (!btn) continue;
    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') continue;
    const rect = btn.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    btn.click();
    return {{ ok: true, selector: sel }};
  }}
  return {{ ok: false, reason: 'stop button not found' }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    return result


def set_composer_text(
    cdp_base: str,
    text: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    mode: str = "replace",
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """Write ``text`` into the composer input.

    ``mode``:
      - ``replace`` (default): wipe then type.
      - ``append``: insert at cursor (keeps existing text).

    We deliberately use the React-friendly path: focus -> selectAll -> setRange
    so Cursor's controlled input picks up the change (a plain ``textContent``
    assignment would be ignored by React).
    """
    tgt = pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    selectors_json = json.dumps(profile.composer_input_selectors)
    safe_text = json.dumps(text)
    is_replace = "true" if mode == "replace" else "false"
    expr = f"""
(() => {{
  const selectors = {selectors_json};
  let el = null;
  for (const sel of selectors) {{
    el = document.querySelector(sel);
    if (el) break;
  }}
  if (!el) return {{ ok: false, reason: 'composer input not found' }};
  el.focus();
  const doc = el.ownerDocument;
  const selObj = doc.getSelection();
  selObj.removeAllRanges();
  const range = doc.createRange();
  range.selectNodeContents(el);
  selObj.addRange(range);
  if ({is_replace}) {{
    document.execCommand('delete', false);
  }}
  document.execCommand('insertText', false, {safe_text});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  return {{ ok: true, finalText: (el.innerText || el.textContent || '').slice(0, 1000) }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    return result


def capture_screenshot(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    quality: int = 70,
    max_width: int | None = 1280,
    profile: CdpProfile = CURSOR_PROFILE,
) -> bytes | None:
    """Capture the workbench as a JPEG byte string.

    JPEG over PNG for size — we're streaming this to a phone over LAN. Quality
    70 is visually fine for a chat UI and ~5x smaller than PNG.
    """
    tgt = pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return None
    import websocket

    ws = websocket.create_connection(
        tgt.ws_url,
        timeout=15,
        origin="http://127.0.0.1:9222",
        suppress_origin=False,
    )
    try:
        _cdp_send_recv(ws, "Page.enable", None, 1)
        params: dict[str, Any] = {"format": "jpeg", "quality": quality}
        if max_width:
            # Capture at device pixel ratio 1 for predictable size on phone.
            params["clip"] = {"x": 0, "y": 0, "width": max_width, "height": 800, "scale": 1}
        result = _cdp_send_recv(ws, "Page.captureScreenshot", params, 2)
        data = result.get("data")
        if not data:
            return None
        return base64.b64decode(data)
    finally:
        ws.close()


def list_windows(cdp_base: str, *, profile: CdpProfile = CURSOR_PROFILE) -> list[dict]:
    """Return workbench pages with ``mode`` (legacy|agents) and ``foreground``."""
    try:
        targets = list_cdp_targets(cdp_base)
    except Exception:
        return []
    pages = profile.match_pages(targets)
    fg = get_foreground_window_title()
    out = []
    for p in pages:
        title = p.get("title") or "(untitled)"
        out.append(
            {
                "title": title,
                "url": p.get("url") or "",
                "ws_url": p.get("webSocketDebuggerUrl") or "",
                "mode": profile.classify_title(title),
                "foreground": bool(fg and fg.casefold() in title.casefold()),
            }
        )
    return out


def _resolve_target(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> WorkbenchTarget | None:
    return pick_workbench_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )


def list_chat_tabs(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> list[dict]:
    """列出当前窗口的 Agent 对话标签。"""
    tgt = _resolve_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return []
    tab_sel = json.dumps(profile.chat_tab_selectors)
    expr = f"""
(() => {{
  const tabSelectors = {tab_sel};
  const tabs = [];
  const seen = new Set();
  for (const sel of tabSelectors) {{
    for (const el of document.querySelectorAll(sel)) {{
      const title = (
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        el.textContent ||
        ''
      ).trim();
      if (!title || seen.has(title)) continue;
      seen.add(title);
      const selected =
        el.getAttribute('data-selected') === 'true' ||
        el.getAttribute('data-highlighted') === 'true' ||
        el.classList.contains('selected') ||
        el.classList.contains('active') ||
        el.classList.contains('checked') ||
        !!el.closest('.selected, .active, .checked, [data-selected="true"]');
      tabs.push({{ title, selected, selector: sel }});
    }}
  }}
  return tabs;
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    return result if isinstance(result, list) else []


def switch_chat_tab(
    cdp_base: str,
    tab_title: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """按标题子串切换到 Agent 对话标签。"""
    tgt = _resolve_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    needle = json.dumps(tab_title)
    tab_sel = json.dumps(profile.chat_tab_selectors)
    expr = f"""
(() => {{
  const needle = {needle}.toLowerCase();
  const tabSelectors = {tab_sel};
  for (const sel of tabSelectors) {{
    for (const el of document.querySelectorAll(sel)) {{
      const title = (
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        el.textContent ||
        ''
      ).trim();
      if (!title.toLowerCase().includes(needle)) continue;
      el.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});
      el.click();
      return {{ ok: true, title, selector: sel }};
    }}
  }}
  return {{ ok: false, reason: 'chat tab not found: ' + {needle} }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    return result


def read_composer_settings(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """读取当前 composer 的模式、模型、对话标签列表。"""
    tgt = _resolve_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    mode_sel = json.dumps(profile.mode_dropdown_selectors)
    model_sel = json.dumps(profile.model_dropdown_selectors)
    expr = f"""
(() => {{
  let mode = '';
  for (const sel of {mode_sel}) {{
    const el = document.querySelector(sel);
    if (!el) continue;
    mode = el.getAttribute('data-mode') || el.textContent?.trim() || '';
    if (mode) break;
  }}
  let model = '';
  for (const sel of {model_sel}) {{
    const el = document.querySelector(sel);
    if (!el) continue;
    model = (el.textContent || el.getAttribute('aria-label') || '').trim();
    if (model) break;
  }}
  return {{ ok: true, mode, model }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    result["chat_tabs"] = list_chat_tabs(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    return result


def set_composer_mode(
    cdp_base: str,
    mode_label: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """设置 composer 模式（如 Agent / Ask / Edit）。"""
    tgt = _resolve_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    needle = json.dumps(mode_label)
    mode_sel = json.dumps(profile.mode_dropdown_selectors)
    item_sel = json.dumps(profile.mode_menu_item_selectors)
    expr = f"""
(() => {{
  const needle = {needle}.toLowerCase();
  let trigger = null;
  for (const sel of {mode_sel}) {{
    trigger = document.querySelector(sel);
    if (trigger) break;
  }}
  if (!trigger) return {{ ok: false, reason: 'mode dropdown not found' }};
  trigger.click();
  const items = [];
  for (const sel of {item_sel}) {{
    items.push(...document.querySelectorAll(sel));
  }}
  for (const item of items) {{
    const text = (item.textContent || item.getAttribute('aria-label') || '').trim();
    if (!text.toLowerCase().includes(needle)) continue;
    item.click();
    return {{ ok: true, mode: text }};
  }}
  return {{ ok: false, reason: 'mode item not found: ' + {needle} }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    return result


def set_composer_model(
    cdp_base: str,
    model_label: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """设置模型（如 auto / GLM-5.2）。"""
    tgt = _resolve_target(
        cdp_base, window_title=window_title, window_mode=window_mode, profile=profile
    )
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    needle = json.dumps(model_label)
    model_sel = json.dumps(profile.model_dropdown_selectors)
    item_sel = json.dumps(profile.mode_menu_item_selectors)
    expr = f"""
(() => {{
  const needle = {needle}.toLowerCase();
  let trigger = null;
  for (const sel of {model_sel}) {{
    trigger = document.querySelector(sel);
    if (trigger) break;
  }}
  if (!trigger) return {{ ok: false, reason: 'model dropdown not found' }};
  trigger.click();
  const items = [];
  for (const sel of {item_sel}) {{
    items.push(...document.querySelectorAll(sel));
  }}
  for (const item of items) {{
    const text = (item.textContent || item.getAttribute('aria-label') || '').trim();
    if (!text.toLowerCase().includes(needle)) continue;
    item.click();
    return {{ ok: true, model: text }};
  }}
  return {{ ok: false, reason: 'model item not found: ' + {needle} }};
}})()
"""
    result = _cdp_evaluate(tgt.ws_url, expr)
    if not isinstance(result, dict):
        return {"ok": False, "reason": "unexpected evaluate result"}
    return result


def wait_for_agent_idle(
    cdp_base: str,
    *,
    window_title: str | None = None,
    window_mode: str = WINDOW_MODE_AUTO,
    timeout_seconds: int = 3600,
    poll_seconds: float = 3.0,
    profile: CdpProfile = CURSOR_PROFILE,
) -> dict:
    """等待 Agent 运行结束（Stop 按钮消失）。"""
    import time

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = read_status(
            cdp_base,
            window_title=window_title,
            window_mode=window_mode,
            profile=profile,
        )
        if not status.stop_button_present:
            return {"ok": True, "idle": True}
        time.sleep(poll_seconds)
    return {"ok": False, "reason": f"agent still running after {timeout_seconds}s"}
