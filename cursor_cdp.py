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
from typing import Any, Iterable

LOG = logging.getLogger("cursor_cdp")

# Selectors kept in one place so CLI / server / future tests agree.
SEND_BUTTON_SELECTORS: list[str] = [
    'button[aria-label="Send"]',
    'button[aria-label="发送"]',
    ".send-with-mode .anysphere-icon-button",
    ".send-with-mode button",
]

STOP_BUTTON_SELECTORS: list[str] = [
    'button[aria-label="Stop"]',
    'button[aria-label="停止"]',
    '.stop-button[aria-label="Stop"]',
    '.composer-stop-button',
]

COMPOSER_SELECTORS: list[str] = [
    "[data-composer-id]",
    ".composer-bar",
    ".composite.auxiliarybar",
    ".aislash-editor-input",
]

COMPOSER_INPUT_SELECTORS: list[str] = [
    "[data-composer-id] [contenteditable='true']",
    ".composer-bar [contenteditable='true']",
    ".aislash-editor-input",
    ".composite.auxiliarybar [contenteditable='true']",
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

    ws = websocket.create_connection(ws_url, timeout=10)
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


@dataclass
class WorkbenchTarget:
    ws_url: str
    title: str
    url: str


def pick_workbench_target(
    cdp_base: str,
    *,
    window_title: str | None = None,
    prefer_foreground: bool = True,
) -> WorkbenchTarget | None:
    """Pick the best Cursor workbench page.

    Selection order:
    1. Filter by ``window_title`` substring if given.
    2. Prefer one whose title matches the current foreground window.
    3. Prefer one whose Agent panel is already visible + send button present.
    4. Fall back to any one with the Agent panel visible.
    5. Otherwise return the first candidate (best effort).
    """
    try:
        targets = list_cdp_targets(cdp_base)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None

    pages = list_workbench_targets(targets)
    if not pages:
        return None

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
        if tgt.ws_url and _target_ready(tgt.ws_url, need_send_button=True):
            return tgt

    # Pass B: panel visible (button not necessarily present yet).
    for page in pages:
        tgt = to_target(page)
        if tgt.ws_url and _target_ready(tgt.ws_url, need_send_button=False):
            return tgt

    # Pass C: anything.
    for page in pages:
        tgt = to_target(page)
        if tgt.ws_url:
            return tgt
    return None


def _target_ready(ws_url: str, need_send_button: bool) -> bool:
    if not agent_panel_visible_by_ws(ws_url):
        return False
    if not need_send_button:
        return True
    selectors_json = json.dumps(SEND_BUTTON_SELECTORS)
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
def agent_panel_visible_by_ws(ws_url: str) -> bool:
    selectors_json = json.dumps(COMPOSER_SELECTORS)
    expr = f"""
(() => {{
  const selectors = {selectors_json};
  return selectors.some(sel => !!document.querySelector(sel));
}})()
"""
    return bool(_cdp_evaluate(ws_url, expr))


@dataclass
class CursorStatus:
    """Snapshot of the Cursor workbench relevant to the mobile UI."""

    cdp_reachable: bool
    cursor_running: bool
    agent_panel_visible: bool
    send_button_enabled: bool
    stop_button_present: bool
    workbench_title: str
    composer_text: str

    def to_dict(self) -> dict:
        return {
            "cdp_reachable": self.cdp_reachable,
            "cursor_running": self.cursor_running,
            "agent_panel_visible": self.agent_panel_visible,
            "send_button_enabled": self.send_button_enabled,
            "stop_button_present": self.stop_button_present,
            "workbench_title": self.workbench_title,
            "composer_text": self.composer_text,
        }


def read_status(cdp_base: str, *, window_title: str | None = None) -> CursorStatus:
    """Probe the workbench and return a serialisable status snapshot.

    Each step is defensive — if CDP is unreachable we still return a usable
    status object so the UI can render the "offline" state.
    """
    running = is_cursor_running()
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
        )

    tgt = pick_workbench_target(cdp_base, window_title=window_title)
    if not tgt:
        return CursorStatus(
            cdp_reachable=True,
            cursor_running=running,
            agent_panel_visible=False,
            send_button_enabled=False,
            stop_button_present=False,
            workbench_title="",
            composer_text="",
        )

    expr = f"""
(() => {{
  const sendSel = {json.dumps(SEND_BUTTON_SELECTORS)};
  const stopSel = {json.dumps(STOP_BUTTON_SELECTORS)};
  const inputSel = {json.dumps(COMPOSER_INPUT_SELECTORS)};

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
    )


def click_send(cdp_base: str, *, window_title: str | None = None) -> dict:
    """Find and click the Send button. Returns ``{{ok, selector? | reason}}``."""
    tgt = pick_workbench_target(cdp_base, window_title=window_title)
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    selectors_json = json.dumps(SEND_BUTTON_SELECTORS)
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


def click_stop(cdp_base: str, *, window_title: str | None = None) -> dict:
    """Find and click the Stop button (when agent is running)."""
    tgt = pick_workbench_target(cdp_base, window_title=window_title)
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    selectors_json = json.dumps(STOP_BUTTON_SELECTORS)
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
    mode: str = "replace",
) -> dict:
    """Write ``text`` into the composer input.

    ``mode``:
      - ``replace`` (default): wipe then type.
      - ``append``: insert at cursor (keeps existing text).

    We deliberately use the React-friendly path: focus -> selectAll -> setRange
    so Cursor's controlled input picks up the change (a plain ``textContent``
    assignment would be ignored by React).
    """
    tgt = pick_workbench_target(cdp_base, window_title=window_title)
    if not tgt:
        return {"ok": False, "reason": "no workbench target"}
    selectors_json = json.dumps(COMPOSER_INPUT_SELECTORS)
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
    quality: int = 70,
    max_width: int | None = 1280,
) -> bytes | None:
    """Capture the workbench as a JPEG byte string.

    JPEG over PNG for size — we're streaming this to a phone over LAN. Quality
    70 is visually fine for a chat UI and ~5x smaller than PNG.
    """
    tgt = pick_workbench_target(cdp_base, window_title=window_title)
    if not tgt:
        return None
    import websocket

    ws = websocket.create_connection(tgt.ws_url, timeout=15)
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


def list_windows(cdp_base: str) -> list[dict]:
    """Return ``[{title, url, ws_url}]`` for every Cursor workbench page."""
    try:
        targets = list_cdp_targets(cdp_base)
    except Exception:
        return []
    pages = list_workbench_targets(targets)
    fg = get_foreground_window_title()
    out = []
    for p in pages:
        title = p.get("title") or "(untitled)"
        out.append(
            {
                "title": title,
                "url": p.get("url") or "",
                "ws_url": p.get("webSocketDebuggerUrl") or "",
                "foreground": bool(fg and fg.casefold() in title.casefold()),
            }
        )
    return out
