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
import { parsePairingToken, type Pairing } from '../auth/pairing';

// ── Types ─────────────────────────────────────────────────────────────────

interface SessionConfig {
  pairing: Pairing;
  backendUrl: string;
}

interface ExtensionState {
  sessionId: string | null;
  /** Session-scoped credential from the dashboard; sent as ?token= on both sockets. */
  pairing: Pairing | null;
  backendUrl: string;
  frontendUrl: string;
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

let offscreenReady = false;

const state: ExtensionState = {
  sessionId: null,
  pairing: null,
  backendUrl: 'ws://localhost:8000',
  frontendUrl: 'http://localhost:5173',
  connectionState: 'disconnected',
  tabCaptureActive: false,
  micCaptureActive: false,
  codeScrapingActive: false,
  bufferStats: { audioChunks: 0, codeSnapshots: 0, estimatedDurationMs: 0 },
};

// ── Offscreen Ready Handshake ──────────────────────────────────────────────

function sendToOffscreen(message: Record<string, unknown>, maxRetries = 10, delayMs = 100): void {
  let attempts = 0;

  function trySend(): void {
    if (offscreenReady) {
      chrome.runtime.sendMessage(message).catch((err) => {
        console.error('[InterviewPilot] sendToOffscreen failed:', err);
      });
      return;
    }
    attempts++;
    if (attempts >= maxRetries) {
      console.error('[InterviewPilot] Offscreen not ready after', maxRetries, 'retries, sending anyway');
      chrome.runtime.sendMessage(message).catch((err) => {
        console.error('[InterviewPilot] sendToOffscreen fallback failed:', err);
      });
      return;
    }
    setTimeout(trySend, delayMs);
  }

  trySend();
}

// ── Lifecycle ─────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log('[InterviewPilot] Extension installed');
});

// Initialize offline buffer on startup
offlineBuffer.open().catch((err) => {
  console.error('[InterviewPilot] Failed to open offline buffer:', err);
});

// Restore persisted URLs on startup
chrome.storage.local.get(['backendUrl', 'frontendUrl'], (result) => {
  if (result.backendUrl) state.backendUrl = result.backendUrl;
  if (result.frontendUrl) state.frontendUrl = result.frontendUrl;
});

// The pairing token lives in session storage: it survives service-worker
// eviction but not a browser restart, and is never written to disk.
chrome.storage.session.get(['pairingToken'], (result) => {
  const pairing = typeof result.pairingToken === 'string' ? parsePairingToken(result.pairingToken) : null;
  if (pairing) {
    state.pairing = pairing;
    state.sessionId = pairing.sessionId;
  }
});

function pairAndConnect(pairing: Pairing, backendUrl: string, frontendUrl: string | undefined): void {
  if (frontendUrl) state.frontendUrl = frontendUrl;
  connectWebSockets({ pairing, backendUrl });
  chrome.storage.session.set({ pairingToken: pairing.token });
  chrome.storage.local.set({ backendUrl, frontendUrl: state.frontendUrl });
}

// ── WebSocket Management ──────────────────────────────────────────────────

function connectWebSockets(config: SessionConfig): void {
  disconnectWebSockets();

  state.pairing = config.pairing;
  state.sessionId = config.pairing.sessionId;
  state.backendUrl = config.backendUrl;
  const getAuthToken = () => state.pairing?.token ?? null;

  // Audio WebSocket
  audioWs = new WSClient(
    {
      baseUrl: config.backendUrl,
      sessionId: config.pairing.sessionId,
      endpoint: '/ws/audio',
      getAuthToken,
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
      sessionId: config.pairing.sessionId,
      endpoint: '/ws/code',
      getAuthToken,
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

// ── Start Capture (popup button or keyboard shortcut) ────────────────────
//
// Chrome hands out a tab stream only after the user invokes the extension
// on that tab (popup, shortcut), so tab audio can't start on pairing alone.

function getTabStreamId(tabId: number): Promise<string> {
  return new Promise((resolve, reject) => {
    chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, (id) => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(id);
    });
  });
}

/** Start whichever of tab audio and microphone isn't running yet. */
async function startCapture(tabId: number, streamId?: string): Promise<void> {
  if (state.connectionState !== 'connected') {
    console.warn('[InterviewPilot] Not connected; pair with a session before starting capture');
    return;
  }
  const tabStreamId = state.tabCaptureActive ? null : (streamId ?? (await getTabStreamId(tabId)));
  await ensureOffscreen();
  if (tabStreamId) sendToOffscreen({ type: 'offscreen:start-tab-capture', streamId: tabStreamId });
  if (!state.micCaptureActive) sendToOffscreen({ type: 'offscreen:start-mic-capture' });
}

chrome.commands.onCommand.addListener((command, tab) => {
  if (command !== 'start-capture' || !tab?.id) return;
  startCapture(tab.id).catch((err) => console.error('[InterviewPilot] Failed to start capture:', err));
});

// ── Offscreen Document Lifecycle ──────────────────────────────────────────

async function ensureOffscreen(): Promise<void> {
  const contexts = await (chrome.runtime as any).getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
  });
  if (contexts.length > 0) {
    offscreenReady = true;
    return;
  }
  offscreenReady = false;
  await (chrome.offscreen as any).createDocument({
    url: 'dist/src/offscreen/offscreen.html',
    reasons: ['USER_MEDIA', 'AUDIO_PLAYBACK'],
    justification: 'Audio capture and playback for interview transcription',
  });
}

// ── Pairing from the dashboard ────────────────────────────────────────────
//
// Only origins listed under externally_connectable in manifest.json can
// reach this listener; Chrome enforces that before delivery. The dashboard
// URL is taken from the verified sender origin, not from the message.

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'pair' || !sender.origin) {
    sendResponse({ ok: false, error: 'unsupported message' });
    return false;
  }
  const pairing = parsePairingToken(String(message.pairingToken ?? ''));
  if (!pairing) {
    sendResponse({ ok: false, error: 'invalid or expired pairing token' });
    return false;
  }
  const backendUrl = typeof message.backendUrl === 'string' ? message.backendUrl : state.backendUrl;
  pairAndConnect(pairing, backendUrl, sender.origin);
  sendResponse({ ok: true, sessionId: pairing.sessionId });
  return false;
});

// ── Message Handler ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'offscreen:ready':
      offscreenReady = true;
      console.log('[InterviewPilot] Offscreen document ready');
      sendResponse({ status: 'ok' });
      return true;

    case 'get-state':
      // Re-trigger reconnect if WSClients exist but are stuck in reconnecting
      // (setTimeout timers are lost when MV3 service worker goes dormant)
      if (state.connectionState === 'reconnecting' && audioWs) {
        if (audioWs.connectionState !== 'connected' && audioWs.connectionState !== 'connecting') {
          audioWs.connect();
        }
        if (codeWs && codeWs.connectionState !== 'connected' && codeWs.connectionState !== 'connecting') {
          codeWs.connect();
        }
      }
      sendResponse(state);
      return true;

    case 'connect': {
      // From the popup: a pairing code pasted by the user.
      const pairing = parsePairingToken(String(message.config?.pairingToken ?? ''));
      if (!pairing) {
        sendResponse({ status: 'error', error: 'That pairing code is invalid or has expired. Get a new one from the dashboard.' });
        return true;
      }
      pairAndConnect(pairing, message.config.backendUrl, message.config.frontendUrl);
      sendResponse({ status: 'connecting', sessionId: pairing.sessionId });
      return true;
    }

    case 'disconnect':
      disconnectWebSockets();
      state.pairing = null;
      state.sessionId = null;
      chrome.storage.session.remove('pairingToken');
      broadcastState();
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

    case 'mic-capture-failed':
      // Offscreen couldn't get mic permission — open a tab to request it
      console.log('[InterviewPilot] Mic capture failed, opening permission page');
      chrome.tabs.create({
        url: chrome.runtime.getURL('dist/src/mic-permission/mic-permission.html'),
        active: true,
      });
      sendResponse({ status: 'ok' });
      return true;

    case 'mic-permission-granted':
      // Permission granted via tab — now start mic capture in offscreen
      console.log('[InterviewPilot] Mic permission granted, starting capture');
      if (sender.tab?.id) {
        chrome.tabs.remove(sender.tab.id).catch(() => {});
      }
      ensureOffscreen()
        .then(() => sendToOffscreen({ type: 'offscreen:start-mic-capture' }))
        .catch((err) => console.error('[InterviewPilot] Failed to start mic capture:', err));
      sendResponse({ status: 'ok' });
      return true;

    case 'mic-permission-denied':
      console.warn('[InterviewPilot] Mic permission denied by user');
      sendResponse({ status: 'ok' });
      return true;

    case 'audio-chunk': {
      const { channel, timestamp } = message;
      // pcmData arrives as a plain number array (Int16 values) from offscreen
      // because chrome.runtime.sendMessage serializes via JSON.
      // Reconstruct to ArrayBuffer for the audio frame builder.
      const int16 = new Int16Array(message.pcmData);
      handleAudioChunk(int16.buffer, channel, timestamp);
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

    case 'start-capture':
      startCapture(message.tabId, message.streamId).catch((err) =>
        console.error('[InterviewPilot] Failed to start capture:', err),
      );
      sendResponse({ status: 'ok' });
      return true;

    case 'start-tab-capture':
      ensureOffscreen()
        .then(() => {
          sendToOffscreen({ type: 'offscreen:start-tab-capture', streamId: message.streamId });
        })
        .catch((err) => {
          console.error('[InterviewPilot] Failed to start tab capture:', err);
        });
      sendResponse({ status: 'ok' });
      return true;

    case 'stop-tab-capture':
      sendToOffscreen({ type: 'offscreen:stop-tab-capture' });
      sendResponse({ status: 'ok' });
      return true;

    case 'start-mic-capture':
      ensureOffscreen()
        .then(() => sendToOffscreen({ type: 'offscreen:start-mic-capture' }))
        .catch((err) => console.error('[InterviewPilot] Failed to start mic capture:', err));
      sendResponse({ status: 'ok' });
      return true;

    case 'stop-mic-capture':
      sendToOffscreen({ type: 'offscreen:stop-mic-capture' });
      sendResponse({ status: 'ok' });
      return true;

    default:
      return false;
  }
});

// ── Side Panel — auto-enable on Google Meet tabs ──────────────────────────

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (
    changeInfo.status === 'complete' &&
    tab.url?.match(/^https:\/\/meet\.google\.com\//) &&
    state.sessionId
  ) {
    chrome.sidePanel
      .setOptions({
        tabId,
        path: 'dist/src/sidepanel/index.html',
        enabled: true,
      })
      .catch((err) => {
        console.error('[InterviewPilot] Failed to enable side panel:', err);
      });
  }
});
