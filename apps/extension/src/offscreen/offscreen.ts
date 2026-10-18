/**
 * InterviewPilot — Offscreen Document for Audio Capture
 *
 * MV3 requires a persistent offscreen document for MediaStream access.
 * This document captures tab audio (via chromeMediaSource) and mic audio
 * (via getUserMedia), processes through an AudioWorklet, and posts PCM
 * chunks back to the service worker.
 */

import { acceptVideoAnswer, closeAllVideoPeers, offerVideo } from './video-relay';

// ── State ──────────────────────────────────────────────────────────────

interface CaptureState {
  stream: MediaStream | null;
  audioContext: AudioContext | null;
  workletNode: AudioWorkletNode | null;
  audioElement: HTMLAudioElement | null;
}

const tabCapture: CaptureState = { stream: null, audioContext: null, workletNode: null, audioElement: null };
const micCapture: CaptureState = { stream: null, audioContext: null, workletNode: null, audioElement: null };

// ── AudioWorklet processor URL ─────────────────────────────────────────
// MV3 CSP blocks blob: URLs for AudioWorklet modules.
// Use the pre-built audio-processor.js from the extension's own files instead.
const WORKLET_URL = chrome.runtime.getURL('dist/audio/audio-processor.js');

// ── Capture helpers ────────────────────────────────────────────────────

async function setupAudioPipeline(
  stream: MediaStream,
  channel: 'tab' | 'mic',
  state: CaptureState,
): Promise<void> {
  const audioContext = new AudioContext({ sampleRate: 48000 });

  // Offscreen documents have no user interaction, so AudioContext starts suspended.
  // Must explicitly resume before audio will flow through the pipeline.
  if (audioContext.state === 'suspended') {
    await audioContext.resume();
  }

  await audioContext.audioWorklet.addModule(WORKLET_URL);

  const source = audioContext.createMediaStreamSource(stream);
  const workletNode = new AudioWorkletNode(audioContext, 'pcm-chunk-processor');

  workletNode.port.onmessage = (event) => {
    if (event.data.type === 'pcm-chunk') {
      chrome.runtime.sendMessage({
        type: 'audio-chunk',
        pcmData: Array.from(new Int16Array(event.data.pcmData)),
        channel,
        timestamp: event.data.timestamp,
      });
    }
  };

  source.connect(workletNode);
  workletNode.connect(audioContext.destination); // required to keep pipeline alive

  state.stream = stream;
  state.audioContext = audioContext;
  state.workletNode = workletNode;
}

function stopCapture(state: CaptureState): void {
  if (state.audioElement) {
    state.audioElement.pause();
    state.audioElement.srcObject = null;
    state.audioElement = null;
  }
  if (state.workletNode) {
    state.workletNode.disconnect();
    state.workletNode = null;
  }
  if (state.audioContext) {
    state.audioContext.close().catch(() => {});
    state.audioContext = null;
  }
  if (state.stream) {
    state.stream.getTracks().forEach((t) => t.stop());
    state.stream = null;
  }
}

// ── Tab capture ────────────────────────────────────────────────────────

async function startTabCapture(streamId: string): Promise<void> {
  try {
    // Audio and video in one capture: Chrome allows one capture per tab.
    // The video is relayed to the dashboard (video-relay.ts).
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId,
        },
      } as any,
      video: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId,
          maxWidth: 1920,
          maxHeight: 1080,
          maxFrameRate: 30,
        },
      } as any,
    });

    // Play tab audio through an <audio> element.
    // Offscreen AudioContext.destination is a null sink (no speakers),
    // so we need a real <audio> element for the AUDIO_PLAYBACK reason.
    const audio = document.createElement('audio');
    audio.srcObject = new MediaStream(stream.getAudioTracks());
    audio.play().catch((err) => console.warn('[Offscreen] Audio play failed:', err));
    tabCapture.audioElement = audio;

    await setupAudioPipeline(stream, 'tab', tabCapture);
    chrome.runtime.sendMessage({ type: 'tab-capture-started' });
    console.log('[Offscreen] Tab capture started');
  } catch (err: any) {
    console.error('[Offscreen] Tab capture failed:', err);
  }
}

function stopTabCapture(): void {
  closeAllVideoPeers();
  stopCapture(tabCapture);
  chrome.runtime.sendMessage({ type: 'tab-capture-stopped' });
  console.log('[Offscreen] Tab capture stopped');
}

// ── Mic capture ────────────────────────────────────────────────────────

async function startMicCapture(retries = 3, delayMs = 500): Promise<void> {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      await setupAudioPipeline(stream, 'mic', micCapture);
      chrome.runtime.sendMessage({ type: 'mic-capture-started' });
      console.log('[Offscreen] Mic capture started');
      return;
    } catch (err) {
      console.warn(`[Offscreen] Mic capture attempt ${attempt}/${retries} failed:`, err);
      if (attempt < retries) {
        await new Promise((r) => setTimeout(r, delayMs));
      }
    }
  }
  console.error('[Offscreen] Mic capture failed after all retries');
  chrome.runtime.sendMessage({ type: 'mic-capture-failed' });
}

function stopMicCapture(): void {
  stopCapture(micCapture);
  chrome.runtime.sendMessage({ type: 'mic-capture-stopped' });
  console.log('[Offscreen] Mic capture stopped');
}

// ── Message handler ────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  switch (message.type) {
    case 'offscreen:start-tab-capture':
      startTabCapture(message.streamId);
      sendResponse({ status: 'ok' });
      return true;

    case 'offscreen:stop-tab-capture':
      stopTabCapture();
      sendResponse({ status: 'ok' });
      return true;

    case 'offscreen:offer-tab-video':
      offerVideo(message.viewerId, tabCapture.stream).catch((err) =>
        console.error('[Offscreen] Tab video offer failed:', err),
      );
      sendResponse({ status: 'ok' });
      return true;

    case 'offscreen:tab-video-answer':
      acceptVideoAnswer(message.viewerId, message.sdp).catch((err) =>
        console.error('[Offscreen] Tab video answer failed:', err),
      );
      sendResponse({ status: 'ok' });
      return true;

    case 'offscreen:start-mic-capture':
      startMicCapture();
      sendResponse({ status: 'ok' });
      return true;

    case 'offscreen:stop-mic-capture':
      stopMicCapture();
      sendResponse({ status: 'ok' });
      return true;

    default:
      return false;
  }
});

console.log('[Offscreen] Audio capture bridge ready');

// Signal to service worker that the offscreen document is ready to receive messages
chrome.runtime.sendMessage({ type: 'offscreen:ready' }).catch(() => {});
