"""Parse Cursor agent transcripts (``*.jsonl``) into structured messages.

Each line of a transcript file is one JSON object. There are three line shapes:

1. ``{"role": "user"|"assistant", "message": {"content": [...]}}``
   - content is an array of blocks: ``{type:"text"|"tool_use", ...}``.
2. ``{"type": "turn_ended", "status": "success"|"error", "error"?: str}``
3. (rare) tool results / system lines — we ignore unknown shapes gracefully.

We deliberately keep parsing defensive: Cursor may add new block types and we
should never crash on a transcript, only degrade the rendering.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

LOG = logging.getLogger("transcripts")

# Cursor stores transcripts under ``%USERPROFILE%\.cursor\projects\<workspace>``.
# The workspace folder name is derived from the project path with non-alphanums
# replaced by dashes, e.g. ``D:\Seafile\Projects\cursor-use`` ->
# ``d-Seafile-Projects-cursor-use``.
DEFAULT_CURSOR_HOME = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".cursor"


@dataclass
class Block:
    type: str  # "text" | "tool_use" | "tool_result" | unknown
    text: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "text": self.text,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
        }


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    blocks: list[Block] = field(default_factory=list)
    error: str | None = None  # set when followed by a turn_ended error
    ts: str = ""  # ISO8601, best effort

    @property
    def text(self) -> str:
        """Concatenated text blocks — handy for previews / search."""
        return "\n".join(b.text for b in self.blocks if b.type == "text")

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "blocks": [b.to_dict() for b in self.blocks],
            "error": self.error,
            "ts": self.ts,
        }


@dataclass
class Turn:
    """A user→assistant exchange ending at a ``turn_ended`` marker."""

    index: int
    messages: list[Message] = field(default_factory=list)
    status: str = "success"  # "success" | "error" | "partial"
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "status": self.status,
            "error": self.error,
            "messages": [m.to_dict() for m in self.messages],
        }


@dataclass
class Transcript:
    session_id: str
    workspace: str
    path: Path
    turns: list[Turn] = field(default_factory=list)
    mtime: float = 0.0
    size: int = 0

    @property
    def title(self) -> str:
        """First non-empty user text — best-effort session title."""
        for turn in self.turns:
            for msg in turn.messages:
                if msg.role == "user":
                    t = _strip_cursor_wrappers(msg.text).strip()
                    if t:
                        first_line = t.splitlines()[0][:120]
                        return first_line
        return "(empty session)"

    @property
    def updated_at(self) -> str:
        return datetime.fromtimestamp(self.mtime, tz=timezone.utc).isoformat()

    def to_summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "title": self.title,
            "updated_at": self.updated_at,
            "size": self.size,
            "turn_count": len(self.turns),
            "path": str(self.path),
        }

    def to_dict(self) -> dict:
        return {
            **self.to_summary(),
            "turns": [t.to_dict() for t in self.turns],
        }


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def workspace_folder_name(project_path: str | Path) -> str:
    """Reproduce Cursor's workspace folder naming.

    Empirically (verified against the live folder on this machine):
      ``D:\\Seafile\\Projects\\cursor-use`` → ``d-Seafile-Projects-cursor-use``

    Algorithm: lowercase the drive letter, drop the colon, then replace each
    backslash (path separator) with a single dash. Note: ``D:\\`` becomes
    ``d-`` (lowercase D + dash from the backslash), NOT ``d--`` — the colon
    is removed entirely, not replaced.
    """
    p = str(project_path).replace("/", "\\")
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + p[2:]  # drop the colon, keep everything after it
    return p.replace("\\", "-")


def cursor_projects_root(cursor_home: Path | None = None) -> Path:
    return (cursor_home or DEFAULT_CURSOR_HOME) / "projects"


def find_workspace_dir(
    project_path: str | None = None,
    *,
    cursor_home: Path | None = None,
) -> Path | None:
    """Locate the ``.cursor/projects/<workspace>`` directory for a project.

    If ``project_path`` is None we auto-detect. The heuristic: among all
    workspace dirs that actually contain transcript files, pick the one whose
    newest transcript was modified most recently — that's almost certainly the
    project the user is actively working on. (Cursor writes plenty of empty
    workspace dirs for one-off buffers; those are noise.)
    """
    root = cursor_projects_root(cursor_home)
    if not root.exists():
        return None

    if project_path:
        candidate = root / workspace_folder_name(project_path)
        if candidate.exists():
            return candidate
        needle = workspace_folder_name(project_path).casefold()
        for d in root.iterdir():
            if d.is_dir() and d.name.casefold() == needle:
                return d
        return None

    # Auto-detect: only consider dirs that have at least one transcript file,
    # then pick by newest transcript mtime.
    candidates: list[tuple[float, Path]] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        tx_files = list_transcripts(d)
        if tx_files:
            # newest transcript mtime is the workspace's "last active" signal
            candidates.append((tx_files[0].stat().st_mtime, d))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def list_transcripts(workspace_dir: Path) -> list[Path]:
    """All transcript jsonl files in a workspace, newest first."""
    base = workspace_dir / "agent-transcripts"
    if not base.exists():
        return []
    files = sorted(
        (p for p in base.rglob("*.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _parse_block(raw: dict) -> Block:
    btype = raw.get("type", "unknown")
    block = Block(type=btype, raw=raw)
    if btype == "text":
        block.text = str(raw.get("text", ""))
    elif btype == "tool_use":
        block.tool_name = str(raw.get("name", ""))
        block.tool_input = raw.get("input") if isinstance(raw.get("input"), dict) else {}
    elif btype == "tool_result":
        # Cursor sometimes inlines tool results; surface them as text for now.
        content = raw.get("content")
        if isinstance(content, list):
            block.text = "\n".join(
                str(c.get("text", "")) for c in content if isinstance(c, dict)
            )
        elif isinstance(content, str):
            block.text = content
    return block


def _parse_message_line(obj: dict) -> Message | None:
    role = obj.get("role")
    if role not in ("user", "assistant", "system"):
        return None
    message = obj.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    blocks: list[Block] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                blocks.append(_parse_block(item))
    elif isinstance(content, str):
        blocks.append(Block(type="text", text=content))
    return Message(role=role, blocks=blocks)


def _strip_cursor_wrappers(text: str) -> str:
    """Remove Cursor's auto-injected XML wrappers from a user message.

    Cursor wraps mobile/forwarded user input like:
      ``<timestamp>...</timestamp>\\n<user_query>...actual text...</user_query>``
    For display we only want the inner text. If no wrapper is present the
    input is returned unchanged.
    """
    if "<user_query>" not in text:
        return text
    import re

    m = re.search(r"<user_query>\s*([\s\S]*?)\s*</user_query>", text)
    return m.group(1).strip() if m else text


def parse_transcript(path: Path, workspace: str) -> Transcript:
    """Parse a single transcript jsonl into a Transcript object.

    Never raises on malformed lines — logs and skips. Fail-fast is for
    programming errors, not for adversarial input.
    """
    session_id = path.stem
    transcript = Transcript(
        session_id=session_id,
        workspace=workspace,
        path=path,
        mtime=path.stat().st_mtime,
        size=path.stat().st_size,
    )

    current_turn: Turn | None = None
    turn_index = 0

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, 1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                LOG.debug("skip malformed line %d in %s", lineno, path.name)
                continue

            if obj.get("type") == "turn_ended":
                if current_turn is None:
                    # turn marker with no preceding messages — skip.
                    continue
                status = obj.get("status", "success")
                current_turn.status = status
                if status == "error":
                    current_turn.error = obj.get("error") or "unknown error"
                    # Attach the error to the last assistant message for rendering.
                    for msg in reversed(current_turn.messages):
                        if msg.role == "assistant":
                            msg.error = current_turn.error
                            break
                transcript.turns.append(current_turn)
                current_turn = None
                turn_index += 1
                continue

            msg = _parse_message_line(obj)
            if msg is None:
                continue
            if current_turn is None:
                current_turn = Turn(index=turn_index)
            current_turn.messages.append(msg)

    if current_turn is not None:
        # Trailing messages without an explicit turn_ended (live session).
        current_turn.status = "partial"
        transcript.turns.append(current_turn)

    return transcript


def iter_all_transcripts(
    project_path: str | None = None,
    *,
    cursor_home: Path | None = None,
) -> Iterator[Transcript]:
    workspace_dir = find_workspace_dir(project_path, cursor_home=cursor_home)
    if not workspace_dir:
        return
    workspace = workspace_dir.name
    for path in list_transcripts(workspace_dir):
        try:
            yield parse_transcript(path, workspace)
        except Exception as exc:
            LOG.warning("failed to parse %s: %s", path, exc)


def get_transcript(
    session_id: str,
    project_path: str | None = None,
    *,
    cursor_home: Path | None = None,
) -> Transcript | None:
    for t in iter_all_transcripts(project_path, cursor_home=cursor_home):
        if t.session_id == session_id:
            return t
    return None


def latest_transcript_summary(project_path: str | None = None) -> dict | None:
    """Convenience for the status endpoint."""
    workspace_dir = find_workspace_dir(project_path)
    if not workspace_dir:
        return None
    files = list_transcripts(workspace_dir)
    if not files:
        return None
    path = files[0]
    try:
        t = parse_transcript(path, workspace_dir.name)
        return t.to_summary()
    except Exception:
        return None
