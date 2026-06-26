// Live workbench screenshot view + zoom controls.
//
// Receives a JPEG data URL via the screenshot WS push. We deliberately avoid
// an <img> with src=data: re-rendering each second — instead we swap the src
// in place so the browser can decode incrementally without layout thrash.

import { useEffect, useRef } from "react";
import { IconAlert, IconRefresh } from "./Icons";

interface Props {
  screenshot: string | null;
  busy: boolean;
  onRefresh: () => void;
}

export function ScreenView({ screenshot, busy, onRefresh }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);

  // Swap src directly to avoid React reconciliation flicker on data URLs.
  useEffect(() => {
    if (imgRef.current && screenshot) {
      imgRef.current.src = screenshot;
    }
  }, [screenshot]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-auto scrollbar-thin bg-black flex items-center justify-center p-2">
        {screenshot ? (
          <img
            ref={imgRef}
            alt="Cursor workbench"
            className="max-w-full h-auto rounded-lg shadow-2xl select-none"
            draggable={false}
          />
        ) : (
          <Placeholder />
        )}
      </div>

      <div className="flex items-center justify-between px-3 py-2 border-t border-line bg-bg-panel">
        <div className="flex items-center gap-2 text-[11px] text-text-muted">
          {busy ? (
            <>
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-warn animate-pulse-soft" />
              Agent 运行中
            </>
          ) : (
            <>
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-ok" />
              空闲
            </>
          )}
        </div>
        <button
          onClick={onRefresh}
          className="flex items-center gap-1.5 text-[12px] text-text-muted hover:text-text px-2 py-1 rounded-md hover:bg-bg-hover transition-colors"
        >
          <IconRefresh width={14} height={14} />
          刷新
        </button>
      </div>
    </div>
  );
}

function Placeholder() {
  return (
    <div className="flex flex-col items-center gap-3 text-text-faint py-16">
      <IconAlert width={32} height={32} />
      <div className="text-center px-6">
        <p className="text-sm font-medium text-text-muted">无法获取屏幕</p>
        <p className="text-[12px] mt-1 leading-relaxed">
          请确认 Cursor 已用调试端口启动：
          <br />
          <code className="text-[11px] font-mono text-accent">
            Cursor.exe --remote-debugging-port=9222
          </code>
        </p>
      </div>
    </div>
  );
}
