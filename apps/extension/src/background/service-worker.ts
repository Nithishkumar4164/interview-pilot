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
import { canonicalMeetingUrl, meetingCode } from '../meeting/meet-link';
import { CAPTURE_BANNER_ID, injectCaptureBanner, removeCaptureBanner } from '../meeting/capture-banner';
import { MEET_CONTROLS_MESSAGE, meetControls, type MeetControlAction, type MeetControlsState } from '../meeting/meet-controls';
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
  frontendUrl: 'http://interviewpilot.localhost:5173',
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
chrome.storage.session.get(['pairingToken', 'tabCaptureActive'], (result) => {
  const pairing = typeof result.pairingToken === 'string' ? parsePairingToken(result.pairingToken) : null;
  if (pairing) {
    state.pairing = pairing;
    state.sessionId = pairing.sessionId;
  }
  // Capture runs in the offscreen document and outlives this worker.
  if (result.tabCaptureActive === true) state.tabCaptureActive = true;
  broadcastState();
});

function setTabCaptureActive(active: boolean): void {
  state.tabCaptureActive = active;
  chrome.storage.session.set({ tabCaptureActive: active });
}

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
      // The session ended (or its token is no longer valid): stop and let go of the Meet tab.
      onRefused: (reason) => {
        console.log(`[InterviewPilot] Session refused by the server (${reason}); stopping capture`);
        endPairing();
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

/** Leave the session: stop capture, close the sockets, forget the pairing and the Meet tab (left open). */
function endPairing(): void {
  stopAllCapture();
  disconnectWebSockets();
  state.pairing = null;
  state.sessionId = null;
  chrome.storage.session.remove('pairingToken');
  setMeetingTab(null).catch(() => {});
  broadcastState();
}

// ── Meeting tab ───────────────────────────────────────────────────────────
//
// The session's Google Meet tab: opened (or focused) on pairing when the
// dashboard sends the session's Meet link, or adopted when capture starts on
// a Meet tab. Closing it stops capture and pauses the session; starting
// capture again resumes it. Kept in session storage, so it survives this
// worker stopping.

interface MeetingTab {
  tabId: number;
  meetingUrl: string;
}

async function getMeetingTab(): Promise<MeetingTab | null> {
  const { meetingTab } = await chrome.storage.session.get('meetingTab');
  return (meetingTab as MeetingTab | undefined) ?? null;
}

async function setMeetingTab(tab: MeetingTab | null): Promise<void> {
  const previous = await getMeetingTab();
  if (tab) await chrome.storage.session.set({ meetingTab: tab });
  else await chrome.storage.session.remove('meetingTab');
  if (previous && previous.tabId !== tab?.tabId) clearBadge(previous.tabId);
  await updateMeetingBadge();
}

function clearBadge(tabId: number): void {
  chrome.action.setBadgeText({ tabId, text: '' }).catch(() => {});
  chrome.action.setTitle({ tabId, title: 'InterviewPilot' }).catch(() => {});
}

/** On the Meet tab: REC on the icon while capturing; otherwise a prompt to start, on the icon and as a banner in the page. */
async function updateMeetingBadge(): Promise<void> {
  const tab = await getMeetingTab();
  if (!tab) return;
  const capturing = state.tabCaptureActive;
  chrome.scripting
    .executeScript({
      target: { tabId: tab.tabId },
      func: capturing ? removeCaptureBanner : injectCaptureBanner,
      args: [CAPTURE_BANNER_ID],
    })
    .catch(() => {
      // Not loaded yet (a just-opened tab): the tab's load completing shows it.
    });
  // Report Meet's mic/camera/presenting state to the dashboard as it changes.
  runMeetControls(tab.tabId, 'watch').catch(() => {});
  try {
    await chrome.action.setBadgeText({ tabId: tab.tabId, text: capturing ? 'REC' : '!' });
    await chrome.action.setBadgeBackgroundColor({ tabId: tab.tabId, color: capturing ? '#ef4444' : '#f59e0b' });
    await chrome.action.setTitle({
      tabId: tab.tabId,
      title: capturing ? 'InterviewPilot: capturing this meeting' : 'InterviewPilot: press Alt+Shift+I (Option+Shift+I on Mac) to start capture',
    });
  } catch {
    // The tab is gone; onRemoved handles it.
  }
}

/** Focus the session's Meet tab if it's open, otherwise open it. */
async function openMeetingTab(meetingUrl: string): Promise<void> {
  const code = meetingCode(meetingUrl);
  if (!code) return;
  const tabs = await chrome.tabs.query({ url: 'https://meet.google.com/*' });
  let tab = tabs.find((t) => meetingCode(t.url) === code);
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  } else {
    tab = await chrome.tabs.create({ url: meetingUrl, active: true });
  }
  if (!tab?.id) return;
  enableSidePanel(tab.id);
  await setMeetingTab({ tabId: tab.id, meetingUrl });
}

/** Capture started on a Meet tab: that is the session's meeting tab from now on. */
async function adoptMeetingTab(tabId: number): Promise<void> {
  const current = await getMeetingTab();
  if (current?.tabId === tabId) return;
  const tab = await chrome.tabs.get(tabId);
  const meetingUrl = canonicalMeetingUrl(tab.url);
  if (meetingUrl) await setMeetingTab({ tabId, meetingUrl });
}

function stopAllCapture(): void {
  offscreenExists()
    .then((exists) => {
      if (!exists) return;
      chrome.runtime.sendMessage({ type: 'offscreen:stop-tab-capture' }).catch(() => {});
      chrome.runtime.sendMessage({ type: 'offscreen:stop-mic-capture' }).catch(() => {});
    })
    .catch(() => {});
}

/** POST pause/resume for the paired session. Idempotent on the backend; 409 means it isn't live yet. */
async function setSessionPaused(paused: boolean): Promise<void> {
  let pairing = state.pairing;
  if (!pairing) {
    // This worker may have just started for a tab event, before startup restored the pairing.
    const { pairingToken } = await chrome.storage.session.get('pairingToken');
    pairing = typeof pairingToken === 'string' ? parsePairingToken(pairingToken) : null;
  }
  if (!pairing) return;
  const { backendUrl } = await chrome.storage.local.get('backendUrl');
  const httpBase = String(backendUrl ?? state.backendUrl).replace(/^ws/, 'http');
  const action = paused ? 'pause' : 'resume';
  const resp = await fetch(`${httpBase}/api/sessions/${pairing.sessionId}/${action}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${pairing.token}` },
  });
  if (!resp.ok && resp.status !== 409) {
    console.warn(`[InterviewPilot] Session ${action} failed: ${resp.status}`);
  }
}

chrome.tabs.onRemoved.addListener((tabId) => {
  getMeetingTab()
    .then(async (tab) => {
      if (tab?.tabId !== tabId) return;
      console.log('[InterviewPilot] Meet tab closed; stopping capture and pausing the session');
      stopAllCapture();
      await setMeetingTab(null);
      reportMeetControls(NO_MEET_CONTROLS);
      await setSessionPaused(true);
    })
    .catch((err) => console.error('[InterviewPilot] Meet tab close handling failed:', err));
});

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
  postToDashboard(extensionStatus());
}

/** What the dashboard shows in place of its Connect button. */
function extensionStatus(): Record<string, unknown> {
  return {
    type: 'extension-status',
    sessionId: state.sessionId,
    connected: state.connectionState === 'connected',
    capturing: state.tabCaptureActive,
  };
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

/** Start whichever of tab capture (audio and video) and microphone isn't running yet. */
async function startCapture(tabId: number, streamId?: string): Promise<void> {
  if (state.connectionState !== 'connected') {
    console.warn('[InterviewPilot] Not connected; pair with a session before starting capture');
    return;
  }
  const tabStreamId = state.tabCaptureActive ? null : (streamId ?? (await getTabStreamId(tabId)));
  await ensureOffscreen();
  if (tabStreamId) sendToOffscreen({ type: 'offscreen:start-tab-capture', streamId: tabStreamId });
  if (!state.micCaptureActive) sendToOffscreen({ type: 'offscreen:start-mic-capture' });
  await adoptMeetingTab(tabId);
  // Capture restarting after the Meet tab was closed picks the interview back up.
  await setSessionPaused(false);
  // Started from the Meet tab: the interviewer works from the dashboard, so bring it back.
  if (tabStreamId && (await getMeetingTab())?.tabId === tabId) await focusDashboardTab(tabId);
}

// ── The dashboard tab ─────────────────────────────────────────────────────
//
// The tab that paired (Connect extension on the live session page). Capture has
// to be started with the Meet tab in front; once it is running, that tab comes
// back to the front.

interface DashboardTab {
  tabId: number;
  windowId: number;
}

async function setDashboardTab(tab: chrome.tabs.Tab | undefined): Promise<void> {
  if (tab?.id === undefined) return;
  const dashboardTab: DashboardTab = { tabId: tab.id, windowId: tab.windowId };
  await chrome.storage.session.set({ dashboardTab });
}

async function focusDashboardTab(exceptTabId: number): Promise<void> {
  const { dashboardTab } = await chrome.storage.session.get('dashboardTab');
  const target = dashboardTab as DashboardTab | undefined;
  if (!target || target.tabId === exceptTabId) return;
  try {
    await chrome.tabs.update(target.tabId, { active: true });
    await chrome.windows.update(target.windowId, { focused: true });
  } catch {
    // The dashboard tab was closed; stay on Meet.
  }
}

// ── Meeting controls ──────────────────────────────────────────────────────
//
// Mute, camera and present for the session's Meet tab, from the dashboard's
// session bar. meetControls() runs inside the Meet tab and clicks Meet's own
// buttons; a watcher it installs reports state changes back here, which are
// relayed to the dashboard.

const NO_MEET_CONTROLS: MeetControlsState = {
  available: false,
  mic: null,
  camera: null,
  presenting: false,
  canPresent: false,
};

let lastMeetControls: MeetControlsState = NO_MEET_CONTROLS;

function reportMeetControls(controls: MeetControlsState): void {
  lastMeetControls = controls;
  postToDashboard({ type: 'meet-controls', state: controls });
}

async function runMeetControls(tabId: number, action: MeetControlAction): Promise<MeetControlsState> {
  const [result] = await chrome.scripting.executeScript({ target: { tabId }, func: meetControls, args: [action] });
  return (result?.result as MeetControlsState | undefined) ?? NO_MEET_CONTROLS;
}

/** A control from the dashboard. Presenting needs a real click in Meet, so it brings Meet forward with its button highlighted. */
async function handleMeetControl(action: unknown): Promise<void> {
  const meeting = await getMeetingTab();
  if (!meeting) {
    reportMeetControls(NO_MEET_CONTROLS);
    return;
  }
  if (action === 'toggle-mic' || action === 'toggle-camera') {
    reportMeetControls(await runMeetControls(meeting.tabId, action));
  } else if (action === 'present') {
    const tab = await chrome.tabs.update(meeting.tabId, { active: true });
    if (tab) await chrome.windows.update(tab.windowId, { focused: true });
    reportMeetControls(await runMeetControls(meeting.tabId, 'highlight-share'));
  } else if (action === 'refresh') {
    reportMeetControls(await runMeetControls(meeting.tabId, 'read'));
  }
}

// ── Tab video for the dashboard ───────────────────────────────────────────
//
// The live session page holds a port open to the extension and introduces
// itself with a viewer ID. The offscreen document owns the one tab capture
// and offers its video to that viewer over WebRTC; this worker only relays
// the offer and the answer. The WebRTC connection runs page to page, so it
// survives this worker stopping.

interface DashboardViewer {
  port: chrome.runtime.Port;
  viewerId: string;
}

let dashboardViewer: DashboardViewer | null = null;

async function offscreenExists(): Promise<boolean> {
  const contexts = await (chrome.runtime as any).getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  return contexts.length > 0;
}

function requestTabVideo(viewer: DashboardViewer): void {
  offscreenExists()
    .then((exists) => {
      // No offscreen document means nothing is being captured yet; capture start will offer.
      if (exists) chrome.runtime.sendMessage({ type: 'offscreen:offer-tab-video', viewerId: viewer.viewerId });
    })
    .catch((err) => console.error('[InterviewPilot] Tab video request failed:', err));
}

chrome.runtime.onConnectExternal.addListener((port) => {
  if (port.name !== 'dashboard') {
    port.disconnect();
    return;
  }
  port.onMessage.addListener((message) => {
    if (message?.type === 'hello' && typeof message.viewerId === 'string') {
      dashboardViewer = { port, viewerId: message.viewerId };
      postToDashboard(extensionStatus());
      if (message.wantsVideo && message.sessionId === state.sessionId) requestTabVideo(dashboardViewer);
      // This worker may have restarted since the last report: read Meet afresh.
      postToDashboard({ type: 'meet-controls', state: lastMeetControls });
      handleMeetControl('refresh').catch(() => reportMeetControls(NO_MEET_CONTROLS));
    } else if (message?.type === 'tab-video-answer' && message.viewerId === dashboardViewer?.viewerId) {
      chrome.runtime.sendMessage({ type: 'offscreen:tab-video-answer', viewerId: message.viewerId, sdp: message.sdp });
    } else if (message?.type === 'meet-control' && port === dashboardViewer?.port) {
      handleMeetControl(message.action).catch((err) => {
        console.error('[InterviewPilot] Meeting control failed:', err);
        reportMeetControls(NO_MEET_CONTROLS);
      });
    }
  });
  port.onDisconnect.addListener(() => {
    if (dashboardViewer?.port === port) dashboardViewer = null;
  });
});

function postToDashboard(message: Record<string, unknown>): void {
  try {
    dashboardViewer?.port.postMessage(message);
  } catch {
    // The page went away; its port's onDisconnect clears the viewer.
  }
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
    reasons: ['USER_MEDIA', 'AUDIO_PLAYBACK', 'WEB_RTC'],
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
  setDashboardTab(sender.tab).catch(() => {});
  // The session's Meet link: open (or focus) that tab and track it for the session.
  const meetingUrl = canonicalMeetingUrl(typeof message.meetingUrl === 'string' ? message.meetingUrl : null);
  if (meetingUrl) {
    openMeetingTab(meetingUrl).catch((err) => console.error('[InterviewPilot] Could not open the Meet tab:', err));
  }
  sendResponse({ ok: true, sessionId: pairing.sessionId });
  return false;
});

// ── Message Handler ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === MEET_CONTROLS_MESSAGE && sender.tab?.id !== undefined) {
    const fromTab = sender.tab.id;
    getMeetingTab()
      .then((meeting) => {
        if (meeting?.tabId === fromTab) reportMeetControls(message.state as MeetControlsState);
      })
      .catch(() => {});
    return false;
  }
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
      endPairing();
      sendResponse({ status: 'disconnected' });
      return true;

    case 'tab-capture-started':
      setTabCaptureActive(true);
      broadcastState();
      updateMeetingBadge().catch(() => {});
      if (dashboardViewer) requestTabVideo(dashboardViewer);
      sendResponse({ status: 'ok' });
      return true;

    case 'tab-capture-stopped':
      setTabCaptureActive(false);
      broadcastState();
      updateMeetingBadge().catch(() => {});
      postToDashboard({ type: 'tab-video-ended' });
      sendResponse({ status: 'ok' });
      return true;

    case 'tab-video-offer':
      // From the offscreen document, for the viewer it names.
      if (message.viewerId === dashboardViewer?.viewerId) {
        postToDashboard({ type: 'tab-video-offer', sdp: message.sdp, sessionId: state.sessionId });
      }
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

function enableSidePanel(tabId: number): void {
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

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url?.match(/^https:\/\/meet\.google\.com\//) && state.sessionId) {
    enableSidePanel(tabId);
  }
  // The session's Meet tab finished loading: (re)show the start-capture prompt.
  if (changeInfo.status === 'complete') {
    getMeetingTab()
      .then((meeting) => {
        if (meeting?.tabId === tabId) return updateMeetingBadge();
      })
      .catch(() => {});
  }
});
