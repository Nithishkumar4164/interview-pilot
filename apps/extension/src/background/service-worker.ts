/**
 * InterviewPilot — Background Service Worker
 *
 * Central orchestrator for:
 * - WebSocket connections (audio + code channels)
 * - Audio capture coordination (tab + mic)
 * - Code snapshot relay from content scripts
 * - Offline buffering and reconnect drain
 * - State management via chrome.storage
 */

import { WSClient, createAudioFrame, type ConnectionState } from '../networking/ws-client';
import { ChunkQueue, type QueuedChunk } from '../networking/chunk-queue';
import { OfflineBuffer } from '../storage/offline-buffer';

// ── Types ─────────────────────────────────────────────────────────────────

interface SessionConfig {
  sessionId: string;
  backendUrl: string;
}

interface ExtensionState {
  sessionId: string | null;
  backendUrl: string;
  connectionState: ConnectionState;
  tabCaptureActive: boolean;
  micCaptureActive: boolean;
  codeScrapingActive: boolean;
  bufferStats: {
    audioChunks: number;
    codeSnapshots: number;
    estimatedDurationMs: number;
  };
}

// ── State ─────────────────────────────────────────────────────────────────

let audioWs: WSClient | null = null;
let codeWs: WSClient | null = null;
const offlineBuffer = new OfflineBuffer();
const drainQueue = new ChunkQueue();

const state: ExtensionState = {
  sessionId: null,
  backendUrl: 'ws://localhost:8000',
  connectionState: 'disconnected',
  tabCaptureActive: false,
  micCaptureActive: false,
  codeScrapingActive: false,
  bufferStats: { audioChunks: 0, codeSnapshots: 0, estimatedDurationMs: 0 },
};

// ── Lifecycle ─────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log('[InterviewPilot] Extension installed');
});

// Initialize offline buffer on startup
offlineBuffer.open().catch((err) => {
  console.error('[InterviewPilot] Failed to open offline buffer:', err);
});

// ── WebSocket Management ──────────────────────────────────────────────────

function connectWebSockets(config: SessionConfig): void {
  disconnectWebSockets();

  state.sessionId = config.sessionId;
  state.backendUrl = config.backendUrl;

  // Audio WebSocket
  audioWs = new WSClient(
    {
      baseUrl: config.backendUrl,
      sessionId: config.sessionId,
      endpoint: '/ws/audio',
    },
    {
      onStateChange: (wsState) => {
        state.connectionState = wsState;
        broadcastState();

        if (wsState === 'connected') {
          drainOfflineBuffer();
        }
      },
      onError: (err) => {
        console.error('[InterviewPilot] Audio WS error:', err);
      },
      onReconnectAttempt: (attempt, delay) => {
        console.log(`[InterviewPilot] Audio WS reconnect attempt ${attempt} in ${delay}ms`);
      },
    },
  );

  // Code WebSocket
  codeWs = new WSClient(
    {
      baseUrl: config.backendUrl,
      sessionId: config.sessionId,
      endpoint: '/ws/code',
    },
    {
      onStateChange: (wsState) => {
        // Use audio WS state as the primary connection indicator
      },
      onError: (err) => {
        console.error('[InterviewPilot] Code WS error:', err);
      },
    },
  );

  audioWs.connect();
  codeWs.connect();
}

function disconnectWebSockets(): void {
  audioWs?.disconnect();
  codeWs?.disconnect();
  audioWs = null;
  codeWs = null;
  state.connectionState = 'disconnected';
  broadcastState();
}

// ── Audio Handling ────────────────────────────────────────────────────────

function handleAudioChunk(
  pcmData: ArrayBuffer,
  channel: 'tab' | 'mic',
  timestamp: number,
): void {
  const frame = createAudioFrame(pcmData, channel, timestamp, 16000, false);

  if (audioWs?.connectionState === 'connected') {
    const sent = audioWs.sendBinary(frame);
    if (!sent) {
      bufferAudioChunk(pcmData, channel, timestamp);
    }
  } else {
    bufferAudioChunk(pcmData, channel, timestamp);
  }
}

async function bufferAudioChunk(
  pcmData: ArrayBuffer,
  channel: 'tab' | 'mic',
  timestamp: number,
): Promise<void> {
  try {
    await offlineBuffer.addAudioChunk({
      pcmData,
      channel,
      timestamp,
      sampleRate: 16000,
    });
    await updateBufferStats();
  } catch (err) {
    console.error('[InterviewPilot] Failed to buffer audio chunk:', err);
  }
}

// ── Code Handling ─────────────────────────────────────────────────────────

function handleCodeSnapshot(snapshot: {
  code: string;
  language: string;
  source: string;
  diff: string | null;
  timestamp: number;
}): void {
  const message = {
    type: 'code_update',
    session_id: state.sessionId,
    timestamp: new Date(snapshot.timestamp).toISOString(),
    payload: {
      code: snapshot.code,
      language: snapshot.language,
      source: snapshot.source,
      diff: snapshot.diff,
      buffered: false,
    },
  };

  if (codeWs?.connectionState === 'connected') {
    const sent = codeWs.sendJSON(message);
    if (!sent) {
      bufferCodeSnapshot(snapshot);
    }
  } else {
    bufferCodeSnapshot(snapshot);
  }
}

async function bufferCodeSnapshot(snapshot: {
  code: string;
  language: string;
  source: string;
  diff: string | null;
  timestamp: number;
}): Promise<void> {
  try {
    await offlineBuffer.addCodeSnapshot(snapshot);
    await updateBufferStats();
  } catch (err) {
    console.error('[InterviewPilot] Failed to buffer code snapshot:', err);
  }
}

// ── Offline Buffer Drain ──────────────────────────────────────────────────

async function drainOfflineBuffer(): Promise<void> {
  try {
    const { audioChunks, codeSnapshots } = await offlineBuffer.drainAll();

    if (audioChunks.length === 0 && codeSnapshots.length === 0) {
      return;
    }

    console.log(
      `[InterviewPilot] Draining buffer: ${audioChunks.length} audio, ${codeSnapshots.length} code`,
    );

    // Prepare audio chunks for drain queue
    const audioQueued: QueuedChunk[] = audioChunks.map((chunk) => ({
      data: createAudioFrame(chunk.pcmData, chunk.channel, chunk.timestamp, chunk.sampleRate, true),
      timestamp: chunk.timestamp,
      type: 'audio' as const,
    }));

    // Prepare code snapshots for drain queue
    const codeQueued: QueuedChunk[] = codeSnapshots.map((snapshot) => ({
      data: new TextEncoder().encode(
        JSON.stringify({
          type: 'code_update',
          session_id: state.sessionId,
          timestamp: new Date(snapshot.timestamp).toISOString(),
          payload: {
            code: snapshot.code,
            language: snapshot.language,
            source: snapshot.source,
            diff: snapshot.diff,
            buffered: true,
          },
        }),
      ).buffer,
      timestamp: snapshot.timestamp,
      type: 'code' as const,
    }));

    // Merge and sort chronologically
    drainQueue.enqueueAll([...audioQueued, ...codeQueued]);

    // Start draining with rate limiting
    drainQueue.startDrain({
      chunksPerSecond: 20,
      onChunk: (chunk) => {
        if (chunk.type === 'audio') {
          return audioWs?.sendBinary(chunk.data) ?? false;
        } else {
          const text = new TextDecoder().decode(chunk.data);
          return codeWs?.sendJSON(JSON.parse(text)) ?? false;
        }
      },
      onComplete: async (sent, failed) => {
        console.log(`[InterviewPilot] Drain complete: ${sent} sent, ${failed} failed`);
        if (failed === 0) {
          await offlineBuffer.clearAll();
          await updateBufferStats();
        }
      },
    });
  } catch (err) {
    console.error('[InterviewPilot] Failed to drain offline buffer:', err);
  }
}

// ── State Broadcasting ────────────────────────────────────────────────────

function broadcastState(): void {
  chrome.runtime.sendMessage({ type: 'state-update', state }).catch(() => {
    // Popup might not be open
  });
}

async function updateBufferStats(): Promise<void> {
  try {
    const stats = await offlineBuffer.getStats();
    state.bufferStats = {
      audioChunks: stats.audioChunks,
      codeSnapshots: stats.codeSnapshots,
      estimatedDurationMs: stats.estimatedAudioDurationMs,
    };
    broadcastState();
  } catch {
    // Ignore stats errors
  }
}

// ── Message Handler ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'get-state':
      sendResponse(state);
      return true;

    case 'connect': {
      const config: SessionConfig = message.config;
      connectWebSockets(config);
      sendResponse({ status: 'connecting' });
      return true;
    }

    case 'disconnect':
      disconnectWebSockets();
      sendResponse({ status: 'disconnected' });
      return true;

    case 'tab-capture-started':
      state.tabCaptureActive = true;
      broadcastState();
      sendResponse({ status: 'ok' });
      return true;

    case 'tab-capture-stopped':
      state.tabCaptureActive = false;
      broadcastState();
      sendResponse({ status: 'ok' });
      return true;

    case 'mic-capture-started':
      state.micCaptureActive = true;
      broadcastState();
      sendResponse({ status: 'ok' });
      return true;

    case 'mic-capture-stopped':
      state.micCaptureActive = false;
      broadcastState();
      sendResponse({ status: 'ok' });
      return true;

    case 'audio-chunk': {
      const { pcmData, channel, timestamp } = message;
      handleAudioChunk(pcmData, channel, timestamp);
      sendResponse({ status: 'ok' });
      return true;
    }

    case 'code-snapshot':
      handleCodeSnapshot(message.payload);
      sendResponse({ status: 'ok' });
      return true;

    case 'get-buffer-stats':
      updateBufferStats().then(() => {
        sendResponse(state.bufferStats);
      });
      return true;

    case 'clear-buffer':
      offlineBuffer
        .clearAll()
        .then(() => updateBufferStats())
        .then(() => sendResponse({ status: 'cleared' }));
      return true;

    default:
      return false;
  }
});
