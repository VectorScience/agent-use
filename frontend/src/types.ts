// Shared wire types — mirror what server/main.py emits.
// Kept in sync by hand; the backend is the source of truth.

export interface CursorStatus {
  cdp_reachable: boolean;
  cursor_running: boolean;
  agent_panel_visible: boolean;
  send_button_enabled: boolean;
  stop_button_present: boolean;
  workbench_title: string;
  composer_text: string;
  latest_session?: SessionSummary | null;
}

export interface SessionSummary {
  session_id: string;
  workspace: string;
  title: string;
  updated_at: string;
  size: number;
  turn_count: number;
  path: string;
}

export interface Block {
  type: string; // "text" | "tool_use" | "tool_result" | unknown
  text: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
}

export interface Message {
  role: "user" | "assistant" | "system";
  blocks: Block[];
  error: string | null;
  ts: string;
}

export interface Turn {
  index: number;
  status: "success" | "error" | "partial";
  error: string | null;
  messages: Message[];
}

export interface Transcript {
  session_id: string;
  workspace: string;
  title: string;
  updated_at: string;
  size: number;
  turn_count: number;
  path: string;
  turns: Turn[];
}

// WebSocket server -> client messages.
export type ServerMessage =
  | { type: "status"; data: CursorStatus }
  | { type: "screenshot"; data: string | null }
  | { type: "result"; action: string; data: Record<string, unknown> }
  | { type: "error"; error: string };

// WebSocket client -> server commands.
export type ClientCommand =
  | { action: "send" }
  | { action: "stop" }
  | { action: "compose"; text: string; mode: "replace" | "append" }
  | { action: "refresh_screenshot" }
  | { action: "status" };
