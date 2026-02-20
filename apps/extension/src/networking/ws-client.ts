/**
 * WebSocket client with exponential backoff reconnect.
 *
 * Supports binary audio frames with channel metadata headers
 * and JSON message framing for code/control messages.
 */

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

export interface WSClientOptions {
  /** Base URL, e.g. "ws://localhost:8000" */
  baseUrl: string;
  /** Session ID for the WebSocket path */
  sessionId: string;
  /** Endpoint path template, e.g. "/ws/audio" or "/ws/code" */
  endpoint: string;
  /** Initial reconnect delay in ms (default: 1000) */
  initialRetryMs?: number;
  /** Maximum reconnect delay in ms (default: 30000) */
  maxRetryMs?: number;
  /** Maximum number of reconnect attempts (default: 20) */
  maxRetries?: number;
  /** Heartbeat interval in ms (default: 15000) */
  heartbeatIntervalMs?: number;
}

export interface WSClientEvents {
  onStateChange?: (state: ConnectionState) => void;
  onMessage?: (data: ArrayBuffer | string) => void;
  onError?: (error: Event) => void;
  onReconnectAttempt?: (attempt: number, delayMs: number) => void;
}

/**
 * Audio chunk metadata header (prepended to binary audio frames).
 *
 * Binary layout (12 bytes):
 *   [0]     uint8   channel: 0=tab, 1=mic
 *   [1-8]   float64 timestamp: epoch ms (BigEndian)
 *   [9]     uint8   sampleRate high byte (sampleRate >> 8)
 *   [10]    uint8   sampleRate low byte (sampleRate & 0xFF)
 *   [11]    uint8   flags: bit0 = buffered (from offline buffer)
 */
export const AUDIO_HEADER_SIZE = 12;

export function createAudioFrame(
  pcmData: ArrayBuffer,
  channel: 'tab' | 'mic',
  timestamp: number,
  sampleRate: number = 16000,
  buffered: boolean = false,
): ArrayBuffer {
  const header = new ArrayBuffer(AUDIO_HEADER_SIZE);
  const view = new DataView(header);

  view.setUint8(0, channel === 'tab' ? 0 : 1);
  view.setFloat64(1, timestamp, false); // big-endian
  view.setUint8(9, (sampleRate >> 8) & 0xff);
  view.setUint8(10, sampleRate & 0xff);
  view.setUint8(11, buffered ? 1 : 0);

  // Concatenate header + pcm data
  const frame = new ArrayBuffer(AUDIO_HEADER_SIZE + pcmData.byteLength);
  const frameView = new Uint8Array(frame);
  frameView.set(new Uint8Array(header), 0);
  frameView.set(new Uint8Array(pcmData), AUDIO_HEADER_SIZE);

  return frame;
}

export function parseAudioHeader(frame: ArrayBuffer): {
  channel: 'tab' | 'mic';
  timestamp: number;
  sampleRate: number;
  buffered: boolean;
  pcmData: ArrayBuffer;
} {
  const view = new DataView(frame);
  return {
    channel: view.getUint8(0) === 0 ? 'tab' : 'mic',
    timestamp: view.getFloat64(1, false),
    sampleRate: (view.getUint8(9) << 8) | view.getUint8(10),
    buffered: view.getUint8(11) === 1,
    pcmData: frame.slice(AUDIO_HEADER_SIZE),
  };
}

export class WSClient {
  private ws: WebSocket | null = null;
  private state: ConnectionState = 'disconnected';
  private retryCount = 0;
  private retryTimeout: ReturnType<typeof setTimeout> | null = null;
  private heartbeatInterval: ReturnType<typeof setInterval> | null = null;
  private readonly options: Required<WSClientOptions>;
  private events: WSClientEvents = {};
  private intentionalClose = false;

  constructor(options: WSClientOptions, events?: WSClientEvents) {
    this.options = {
      initialRetryMs: 1000,
      maxRetryMs: 30000,
      maxRetries: 20,
      heartbeatIntervalMs: 15000,
      ...options,
    };
    if (events) {
      this.events = events;
    }
  }

  get connectionState(): ConnectionState {
    return this.state;
  }

  get url(): string {
    return `${this.options.baseUrl}${this.options.endpoint}/${this.options.sessionId}`;
  }

  connect(): void {
    if (this.state === 'connected' || this.state === 'connecting') {
      return;
    }

    this.intentionalClose = false;
    this.setState(this.retryCount > 0 ? 'reconnecting' : 'connecting');

    try {
      this.ws = new WebSocket(this.url);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onopen = () => {
        this.retryCount = 0;
        this.setState('connected');
        this.startHeartbeat();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        this.events.onMessage?.(event.data);
      };

      this.ws.onerror = (event: Event) => {
        this.events.onError?.(event);
      };

      this.ws.onclose = () => {
        this.stopHeartbeat();
        if (!this.intentionalClose) {
          this.setState('reconnecting');
          this.scheduleReconnect();
        } else {
          this.setState('disconnected');
        }
      };
    } catch {
      this.setState('reconnecting');
      this.scheduleReconnect();
    }
  }

  disconnect(): void {
    this.intentionalClose = true;
    this.clearRetryTimeout();
    this.stopHeartbeat();

    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }

    this.retryCount = 0;
    this.setState('disconnected');
  }

  /**
   * Send binary data (audio frames).
   * Returns false if the connection is not open.
   */
  sendBinary(data: ArrayBuffer): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      return false;
    }
    this.ws.send(data);
    return true;
  }

  /**
   * Send a JSON message (code updates, control messages).
   * Returns false if the connection is not open.
   */
  sendJSON(data: Record<string, unknown>): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      return false;
    }
    this.ws.send(JSON.stringify(data));
    return true;
  }

  /** Update event handlers after construction. */
  setEvents(events: WSClientEvents): void {
    this.events = events;
  }

  private setState(newState: ConnectionState): void {
    if (this.state !== newState) {
      this.state = newState;
      this.events.onStateChange?.(newState);
    }
  }

  private scheduleReconnect(): void {
    if (this.retryCount >= this.options.maxRetries) {
      this.setState('disconnected');
      return;
    }

    const delay = Math.min(
      this.options.initialRetryMs * Math.pow(2, this.retryCount),
      this.options.maxRetryMs,
    );
    // Add jitter: ±25%
    const jitter = delay * 0.25 * (Math.random() * 2 - 1);
    const actualDelay = Math.round(delay + jitter);

    this.retryCount++;
    this.events.onReconnectAttempt?.(this.retryCount, actualDelay);

    this.retryTimeout = setTimeout(() => {
      this.connect();
    }, actualDelay);
  }

  private clearRetryTimeout(): void {
    if (this.retryTimeout !== null) {
      clearTimeout(this.retryTimeout);
      this.retryTimeout = null;
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.sendJSON({ type: 'ping', timestamp: Date.now() });
      }
    }, this.options.heartbeatIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatInterval !== null) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }
}
