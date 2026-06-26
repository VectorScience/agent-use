// Top bar: connection pill + workbench title + tab switcher.
// Pure presentational, fully controlled by parent.

import { IconChat, IconDot, IconScreen } from "./Icons";

export type View = "screen" | "chat";

interface Props {
  view: View;
  onViewChange: (v: View) => void;
  connected: boolean;
  cursorRunning: boolean;
  cdpReachable: boolean;
  workbenchTitle: string;
  agentBusy: boolean;
}

export function Header({
  view,
  onViewChange,
  connected,
  cursorRunning,
  cdpReachable,
  workbenchTitle,
  agentBusy,
}: Props) {
  let dotColor = "bg-err";
  let dotLabel = "离线";
  if (connected && cdpReachable && cursorRunning) {
    dotColor = agentBusy ? "bg-warn animate-pulse-soft" : "bg-ok";
    dotLabel = agentBusy ? "运行中" : "在线";
  } else if (connected) {
    dotColor = "bg-warn";
    dotLabel = cursorRunning ? "CDP 未就绪" : "Cursor 未启动";
  }

  const title = workbenchTitle?.trim() || "Cursor Remote";

  return (
    <header className="sticky top-0 z-30 bg-bg/85 backdrop-blur-md border-b border-line">
      <div className="flex items-center gap-2 px-3 h-12">
        <div className="flex items-center gap-1.5 rounded-full bg-bg-elevated px-2.5 py-1">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor}`} />
          <span className="text-[11px] font-medium text-text-muted">{dotLabel}</span>
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-[13px] font-semibold truncate">{title}</h1>
        </div>
      </div>

      <nav className="flex px-3 gap-1 -mb-px">
        <TabButton
          active={view === "screen"}
          onClick={() => onViewChange("screen")}
          icon={<IconScreen width={16} height={16} />}
          label="屏幕"
        />
        <TabButton
          active={view === "chat"}
          onClick={() => onViewChange("chat")}
          icon={<IconChat width={16} height={16} />}
          label="对话"
        />
      </nav>
    </header>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium border-b-2 transition-colors ${
        active
          ? "border-accent text-text"
          : "border-transparent text-text-muted hover:text-text"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

// Re-export so consumers can import everything from Header.
export { IconDot };
