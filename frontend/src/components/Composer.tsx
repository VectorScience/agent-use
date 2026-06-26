// Bottom composer: textarea + Send / Stop button.
//
// State machine:
// - agentBusy (stop button present)  → show Stop button
// - composer has text                 → show Send button (enabled)
// - composer empty                    → Send disabled
//
// Editing the textarea updates Cursor's actual input via the WS "compose"
// command (debounced). This is the killer feature: type on phone, see it live
// in Cursor's composer.

import { useEffect, useRef, useState } from "react";
import { IconSend, IconStop } from "./Icons";

interface Props {
  agentBusy: boolean;
  remoteComposerText: string;
  connected: boolean;
  onSend: () => void;
  onStop: () => void;
  onTextChange: (text: string) => void;
}

export function Composer({
  agentBusy,
  remoteComposerText,
  connected,
  onSend,
  onStop,
  onTextChange,
}: Props) {
  const [local, setLocal] = useState(remoteComposerText);
  const [dirty, setDirty] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // When Cursor's composer changes from elsewhere (e.g. user typed on PC),
  // sync down — unless the user is actively editing locally.
  useEffect(() => {
    if (!dirty) setLocal(remoteComposerText);
  }, [remoteComposerText, dirty]);

  // Auto-grow textarea up to a max height.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [local]);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const v = e.target.value;
    setLocal(v);
    setDirty(true);
    onTextChange(v);
  }

  function handleSend() {
    if (!local.trim() || agentBusy) return;
    onSend();
    setDirty(false);
  }

  function handleStop() {
    onStop();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter to send, Shift+Enter for newline — matches desktop chat UX.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  }

  const canSend = connected && !!local.trim() && !agentBusy;

  return (
    <div className="bg-bg-panel border-t border-line px-2 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
      <div className="flex items-end gap-2 bg-bg-elevated rounded-2xl border border-line focus-within:border-accent/50 transition-colors px-2.5 py-1.5">
        <textarea
          ref={taRef}
          value={local}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={connected ? "输入消息，回车发送" : "正在连接服务器…"}
          disabled={!connected}
          className="flex-1 bg-transparent resize-none outline-none text-[14px] leading-relaxed text-text placeholder:text-text-faint disabled:opacity-50 py-1 max-h-40 scrollbar-thin"
        />

        {agentBusy ? (
          <button
            onClick={handleStop}
            disabled={!connected}
            aria-label="停止"
            className="flex-shrink-0 w-9 h-9 rounded-full bg-err text-white flex items-center justify-center hover:bg-err/90 active:scale-95 transition disabled:opacity-40"
          >
            <IconStop width={16} height={16} />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!canSend}
            aria-label="发送"
            className="flex-shrink-0 w-9 h-9 rounded-full bg-accent text-white flex items-center justify-center hover:bg-accent-hover active:scale-95 transition disabled:bg-bg-hover disabled:text-text-faint"
          >
            <IconSend width={16} height={16} />
          </button>
        )}
      </div>
    </div>
  );
}
