/**
 * Dashboard WebSocket client with auto-reconnect and state snapshot.
 *
 * Connects to ws://{host}/ws/dashboard/{sessionId} and dispatches
 * typed events to Zustand stores.
 */

export type WSMessageType =
  | 'transcript'
  | 'analysis'
  | 'code_analysis'
  | 'hint'
  | 'runtime_hint'
  | 'red_flag'
  | 'catchup'
  | 'provider_status'
  | 'error'
  | 'speaker_snapshot'
  | 'speaker_transcript_assigned'
  | 'sv_transcripts'
  | 'context_reloaded';

export interface WSMessage<T = unknown> {
  type: WSMessageType;
  session_id: string;
  timestamp: string;
  payload: T;
}

type MessageHandler = (message: WSMessage) => void;

interface WSClientOptions {
  sessionId: string;
  url?: string;
  reconnectIntervalMs?: number;
  maxReconnectIntervalMs?: number;
  heartbeatIntervalMs?: number;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: Event) => void;
}

const DEFAULT_RECONNECT_MS = 1000;
const MAX_RECONNECT_MS = 30000;
const HEARTBEAT_MS = 15000;
const SNAPSHOT_KEY = 'ws_state_snapshot';

export class DashboardWSClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<WSMessageType | '*', Set<MessageHandler>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectDelay: number;
  private options: Required<WSClientOptions>;
  private _connected = false;
  private _intentionalClose = false;
  private _reconnectAttempts = 0;

  constructor(options: WSClientOptions) {
    this.options = {
      url: `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/dashboard/${options.sessionId}`,
      reconnectIntervalMs: DEFAULT_RECONNECT_MS,
      maxReconnectIntervalMs: MAX_RECONNECT_MS,
      heartbeatIntervalMs: HEARTBEAT_MS,
      onOpen: () => {},
      onClose: () => {},
      onError: () => {},
      ...options,
    };
    this.reconnectDelay = this.options.reconnectIntervalMs;
  }

  get connected(): boolean {
    return this._connected;
  }

  get reconnectAttempts(): number {
    return this._reconnectAttempts;
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    this._intentionalClose = false;

    console.log('[WS] Connecting to', this.options.url);

    try {
      this.ws = new WebSocket(this.options.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this._connected = true;
      this._reconnectAttempts = 0;
      this.reconnectDelay = this.options.reconnectIntervalMs;
      this.startHeartbeat();
      this.options.onOpen();
    };

    this.ws.onclose = (event) => {
      console.log('[WS] Closed:', event.code, event.reason || '(no reason)');
      this._connected = false;
      this.stopHeartbeat();
      this.options.onClose();

      if (!this._intentionalClose) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (event) => {
      console.error('[WS] Error:', event);
      this.options.onError(event);
    };

    this.ws.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data);
        console.debug('[WS] Message received:', message.type);
        this.dispatch(message);
      } catch {
        // Ignore unparseable messages (e.g., pong)
      }
    };
  }

  disconnect(): void {
    this._intentionalClose = true;
    this.stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._connected = false;
  }

  on(type: WSMessageType | '*', handler: MessageHandler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);

    // Return unsubscribe function
    return () => {
      this.handlers.get(type)?.delete(handler);
    };
  }

  send(data: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  /** Save current state to sessionStorage for fast recovery on reconnect. */
  saveSnapshot(state: Record<string, unknown>): void {
    try {
      sessionStorage.setItem(
        `${SNAPSHOT_KEY}_${this.options.sessionId}`,
        JSON.stringify({ ...state, _snapshotAt: new Date().toISOString() }),
      );
    } catch {
      // sessionStorage may be full or unavailable
    }
  }

  /** Restore state from sessionStorage after reconnect. */
  loadSnapshot(): Record<string, unknown> | null {
    try {
      const raw = sessionStorage.getItem(`${SNAPSHOT_KEY}_${this.options.sessionId}`);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  clearSnapshot(): void {
    sessionStorage.removeItem(`${SNAPSHOT_KEY}_${this.options.sessionId}`);
  }

  private dispatch(message: WSMessage): void {
    // Dispatch to type-specific handlers
    const typeHandlers = this.handlers.get(message.type);
    if (typeHandlers) {
      for (const handler of typeHandlers) {
        handler(message);
      }
    }
    // Dispatch to wildcard handlers
    const wildcardHandlers = this.handlers.get('*');
    if (wildcardHandlers) {
      for (const handler of wildcardHandlers) {
        handler(message);
      }
    }
  }

  private scheduleReconnect(): void {
    if (this._intentionalClose) return;

    this._reconnectAttempts++;
    // Exponential backoff with jitter
    const jitter = 1 + (Math.random() * 0.5 - 0.25);
    const delay = Math.min(this.reconnectDelay * jitter, this.options.maxReconnectIntervalMs);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.options.maxReconnectIntervalMs);

    console.log(`[WS] Reconnect attempt ${this._reconnectAttempts} in ${Math.round(delay)}ms`);

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping', timestamp: new Date().toISOString() });
    }, this.options.heartbeatIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}
