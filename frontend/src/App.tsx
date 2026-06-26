// App shell: holds global state (connection, status, screenshot, view) and
// composes Header + active view + Composer.
//
// State flow:
//   WS push → update state → props down to presentational children
//   Child event → send WS command → server acts on Cursor
//
// The composer text is "doubly sourced": user types locally for snappy UX,
// and we push the text to Cursor's real composer so they see it appear there
// too. We use a debounce so we don't spam the WS on every keystroke.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Connection } from "./api";
import { Header, type View } from "./components/Header";
import { ScreenView } from "./components/ScreenView";
import { ChatView } from "./components/ChatView";
import { Composer } from "./components/Composer";
import type { CursorStatus, ServerMessage } from "./types";

const EMPTY_STATUS: CursorStatus = {
  cdp_reachable: false,
  cursor_running: false,
  agent_panel_visible: false,
  send_button_enabled: false,
  stop_button_present: false,
  workbench_title: "",
  composer_text: "",
  latest_session: null,
};

export default function App() {
  const [view, setView] = useState<View>("screen");
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<CursorStatus>(EMPTY_STATUS);
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [transcriptVersion, setTranscriptVersion] = useState(0);

  // One Connection for the app's lifetime.
  const conn = useMemo(() => new Connection(), []);
  // Latest composer text we pushed to Cursor — used to detect external changes.
  const lastPushedText = useRef("");
  // Tracks whether the user has manually chosen a session. Once true, server
  // status pushes won't yank them away from that choice.
  const userPickedRef = useRef(false);

  useEffect(() => {
    conn.start();
    const offStatus = conn.onStatusChange(setConnected);
    const offMsg = conn.onMessage((msg: ServerMessage) => {
      switch (msg.type) {
        case "status":
          handleStatus(msg.data);
          break;
        case "screenshot":
          setScreenshot(msg.data);
          break;
        case "result":
        case "error":
          // Results are best-effort; status poller will reflect side effects.
          if (msg.type === "error") console.warn("server error:", msg.error);
          break;
      }
    });

    // Hydrate: pick the latest session so ChatView has something to show.
    // This runs once; subsequent session selection is driven by user choice
    // or by the status poller noticing a brand-new session.
    api
      .sessions()
      .then((s) => {
        if (s.length > 0 && !userPickedRef.current && currentSessionId === null) {
          setCurrentSessionId(s[0].session_id);
        }
      })
      .catch(() => {});

    return () => {
      offStatus();
      offMsg();
      conn.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn]);

  function handleStatus(s: CursorStatus) {
    setStatus(s);

    // Track transcript changes so ChatView knows to re-fetch.
    // Only auto-switch if the user hasn't manually picked a session.
    const newId = s.latest_session?.session_id ?? null;
    if (newId && newId !== currentSessionId && !userPickedRef.current) {
      setCurrentSessionId(newId);
      setTranscriptVersion((v) => v + 1);
    }

    // Heuristic: if the composer text just became empty AND we had text
    // before, a turn likely started → bump version to refresh chat.
    if (s.composer_text === "" && lastPushedText.current !== "") {
      setTranscriptVersion((v) => v + 1);
    }
    lastPushedText.current = s.composer_text;
  }

  const handleSend = useCallback(() => {
    conn.send({ action: "send" });
    // Optimistically mark transcript dirty so chat refreshes soon.
    setTimeout(() => setTranscriptVersion((v) => v + 1), 1500);
  }, [conn]);

  const handleStop = useCallback(() => {
    conn.send({ action: "stop" });
  }, [conn]);

  // When user manually picks a session, remember so we don't auto-override.
  const handlePickSession = useCallback((id: string) => {
    userPickedRef.current = true;
    setCurrentSessionId(id);
  }, []);

  // Debounced composer sync. We use a ref so each keystroke doesn't recreate
  // the timer.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleTextChange = useCallback(
    (text: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        conn.send({ action: "compose", text, mode: "replace" });
        lastPushedText.current = text;
      }, 400);
    },
    [conn],
  );

  const handleRefreshScreenshot = useCallback(() => {
    conn.send({ action: "refresh_screenshot" });
  }, [conn]);

  const agentBusy = status.stop_button_present;

  return (
    <div className="flex flex-col h-full max-w-[680px] mx-auto bg-bg">
      <Header
        view={view}
        onViewChange={setView}
        connected={connected}
        cursorRunning={status.cursor_running}
        cdpReachable={status.cdp_reachable}
        workbenchTitle={status.workbench_title}
        agentBusy={agentBusy}
      />

      <main className="flex-1 min-h-0 flex flex-col">
        {view === "screen" ? (
          <ScreenView screenshot={screenshot} busy={agentBusy} onRefresh={handleRefreshScreenshot} />
        ) : (
          <ChatView
            currentSessionId={currentSessionId}
            onPickSession={handlePickSession}
            transcriptVersion={transcriptVersion}
          />
        )}
      </main>

      <Composer
        agentBusy={agentBusy}
        remoteComposerText={status.composer_text}
        connected={connected}
        onSend={handleSend}
        onStop={handleStop}
        onTextChange={handleTextChange}
      />
    </div>
  );
}
