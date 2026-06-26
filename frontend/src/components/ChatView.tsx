// Full chat view: session picker + scrollable message list.
//
// Loads the latest session on mount, lets user switch via the drawer. We fetch
// the full transcript via REST on session change, and rely on the WS
// "status.latest_session" push to detect when a new turn arrives so we can
// re-fetch incrementally.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { SessionSummary, Transcript } from "../types";
import { MessageBubble } from "./MessageBubble";
import { IconChevronLeft, IconRefresh } from "./Icons";

interface Props {
  currentSessionId: string | null;
  onPickSession: (id: string) => void;
  /** Bumped by parent whenever a turn_ended is suspected (status changed). */
  transcriptVersion: number;
}

export function ChatView({ currentSessionId, onPickSession, transcriptVersion }: Props) {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDrawer, setShowDrawer] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);

  const fetchSession = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const t = await api.session(id);
      setTranscript(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!currentSessionId) return;
    fetchSession(currentSessionId);
  }, [currentSessionId, fetchSession, transcriptVersion]);

  // Auto-scroll to bottom when new content arrives, but only if the user is
  // already near the bottom (don't yank them while reading history).
  useEffect(() => {
    if (!stickToBottom) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript, stickToBottom]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setStickToBottom(nearBottom);
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-text-muted gap-2 p-6">
        <p className="text-sm">加载失败：{error}</p>
        {currentSessionId && (
          <button
            onClick={() => fetchSession(currentSessionId)}
            className="text-[12px] text-accent"
          >
            重试
          </button>
        )}
      </div>
    );
  }

  if (!currentSessionId) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-faint text-sm p-6 text-center">
        暂无对话记录。
        <br />
        在 Cursor 里发起一次 Agent 会话后再回来。
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 relative">
      <SessionBar
        transcript={transcript}
        loading={loading}
        onOpenDrawer={() => setShowDrawer(true)}
        onRefresh={() => currentSessionId && fetchSession(currentSessionId)}
      />

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto scrollbar-thin px-3 py-2"
      >
        {transcript?.turns.map((turn) => (
          <TurnBlock key={turn.index} turn={turn} />
        ))}
        {loading && (
          <div className="text-center text-[11px] text-text-faint py-2">加载中…</div>
        )}
      </div>

      {showDrawer && (
        <SessionDrawer
          currentId={currentSessionId}
          onClose={() => setShowDrawer(false)}
          onPick={(id) => {
            onPickSession(id);
            setShowDrawer(false);
          }}
        />
      )}
    </div>
  );
}

function SessionBar({
  transcript,
  loading,
  onOpenDrawer,
  onRefresh,
}: {
  transcript: Transcript | null;
  loading: boolean;
  onOpenDrawer: () => void;
  onRefresh: () => void;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-line bg-bg-panel">
      <button
        onClick={onOpenDrawer}
        className="flex-1 min-w-0 flex items-center gap-2 text-left rounded-md px-2 py-1 hover:bg-bg-hover transition-colors"
      >
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px] font-medium truncate">
            {transcript?.title ?? "(no session)"}
          </div>
          <div className="text-[10.5px] text-text-faint">
            {transcript ? `${transcript.turn_count} 轮` : loading ? "加载中…" : ""}
          </div>
        </div>
        <span className="text-text-faint text-[10px]">会话列表 ›</span>
      </button>
      <button
        onClick={onRefresh}
        aria-label="刷新对话"
        className="p-1.5 rounded-md text-text-muted hover:text-text hover:bg-bg-hover"
      >
        <IconRefresh width={15} height={15} />
      </button>
    </div>
  );
}

function TurnBlock({ turn }: { turn: Transcript["turns"][number] }) {
  return (
    <div className="mb-2">
      {turn.messages.map((m, i) => (
        <MessageBubble key={i} message={m} />
      ))}
    </div>
  );
}

function SessionDrawer({
  currentId,
  onClose,
  onPick,
}: {
  currentId: string;
  onClose: () => void;
  onPick: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .sessions()
      .then(setSessions)
      .catch(() => setSessions([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-bg/95 backdrop-blur-sm animate-in">
      <div className="flex items-center gap-2 px-3 h-12 border-b border-line">
        <button onClick={onClose} className="p-1 text-text-muted hover:text-text">
          <IconChevronLeft width={20} height={20} />
        </button>
        <h2 className="text-[13px] font-semibold">历史会话</h2>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {loading && <div className="text-center text-[12px] text-text-faint py-4">加载中…</div>}
        {!loading && sessions.length === 0 && (
          <div className="text-center text-[12px] text-text-faint py-4">无会话</div>
        )}
        {sessions.map((s) => (
          <button
            key={s.session_id}
            onClick={() => onPick(s.session_id)}
            className={`w-full text-left px-4 py-3 border-b border-line/60 hover:bg-bg-hover transition-colors ${
              s.session_id === currentId ? "bg-bg-elevated" : ""
            }`}
          >
            <div className="text-[13px] font-medium truncate mb-0.5">{s.title}</div>
            <div className="flex items-center gap-2 text-[10.5px] text-text-faint">
              <span>{s.turn_count} 轮</span>
              <span>·</span>
              <span>{formatRelative(s.updated_at)}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} 天前`;
  return d.toLocaleDateString();
}
