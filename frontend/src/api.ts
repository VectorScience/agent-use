// Thin API + WS client. Single responsibility: talk to the backend.
//
// In dev, Vite proxies /api and /ws to FastAPI. In Tauri / production we use
// the same origin. If VITE_API_BASE is set (e.g. by Tauri pointing at the
// phone-side embedded URL), use that.

import type {
  ClientCommand,
  CursorStatus,
  ServerMessage,
  SessionSummary,
  Transcript,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

function wsUrl(): string {
  if (API_BASE) {
    return `${API_BASE.replace(/^http/, "ws")}/ws`;
  }
  // Dev (Vite proxy) and same-origin production both use a relative path.
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(apiUrl(path), { headers: { Accept: "application/json" } });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText} on ${path}`);
  return (await resp.json()) as T;
}

export const api = {
  status: () => getJson<CursorStatus>("/api/status"),
  sessions: () => getJson<{ sessions: SessionSummary[] }>("/api/sessions").then((r) => r.sessions),
  session: (id: string) => getJson<Transcript>(`/api/sessions/${encodeURIComponent(id)}`),
  screenshotUrl: () => apiUrl("/api/screenshot.jpg"),
};

export type ConnectionListener = (msg: ServerMessage) => void;
export type ConnectionStatusListener = (connected: boolean) => void;

/**
 * Reconnecting WebSocket wrapper.
 *
 * Why a class: the socket has lifecycle (connect/reconnect/disconnect) that's
 * awkward to express with bare hooks. We expose subscribe() so React can stay
 * declarative on top of an imperative connection.
 */
export class Connection {
  private ws: WebSocket | null = null;
  private listeners = new Set<ConnectionListener>();
  private statusListeners = new Set<ConnectionStatusListener>();
  private reconnectDelayMs = 1000;
  private shouldRun = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  start(): void {
    this.shouldRun = true;
    this.connect();
  }

  stop(): void {
    this.shouldRun = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  onMessage(fn: ConnectionListener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStatusChange(fn: ConnectionStatusListener): () => void {
    this.statusListeners.add(fn);
    fn(this.ws?.readyState === WebSocket.OPEN);
    return () => this.statusListeners.delete(fn);
  }

  send(cmd: ClientCommand): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(cmd));
    }
  }

  private connect(): void {
    try {
      const ws = new WebSocket(wsUrl());
      this.ws = ws;
      ws.onopen = () => {
        this.reconnectDelayMs = 1000;
        this.emitStatus(true);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as ServerMessage;
          this.listeners.forEach((l) => l(msg));
        } catch (err) {
          console.warn("invalid ws frame", err);
        }
      };
      ws.onclose = () => {
        this.emitStatus(false);
        this.scheduleReconnect();
      };
      ws.onerror = () => {
        // onclose will follow and trigger reconnect.
      };
    } catch (err) {
      console.warn("ws connect failed", err);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (!this.shouldRun) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    // Cap exponential backoff at 10s — typical mobile network blip shouldn't
    // trigger a 30s wait.
    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 1.5, 10000);
    this.reconnectTimer = setTimeout(() => this.connect(), this.reconnectDelayMs);
  }

  private emitStatus(connected: boolean): void {
    this.statusListeners.forEach((l) => l(connected));
  }
}
