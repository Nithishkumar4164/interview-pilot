/**
 * The live session page's channel to the Chrome extension: meeting video and
 * meeting controls.
 *
 * The page keeps a port open to the extension (it lists this origin under
 * externally_connectable). The extension captures the meeting tab once, for
 * audio and video, and offers the video to this page over a local WebRTC
 * connection, so the dashboard shows it without the screen-share picker. It
 * also reports the Meet tab's mic, camera and presenting state, and takes
 * mute / camera / present requests back.
 */
import { useCallback, useEffect, useRef } from 'react';

interface ExtensionPort {
  onMessage: { addListener: (cb: (message: unknown) => void) => void };
  onDisconnect: { addListener: (cb: () => void) => void };
  postMessage: (message: unknown) => void;
  disconnect: () => void;
}

interface ChromeRuntime {
  connect?: (extensionId: string, info: { name: string }) => ExtensionPort;
}

/** The Meet tab's controls, as the extension reads them from Meet. */
export interface MeetControlsState {
  /** Meet's call controls were found (the session's Meet tab is open). */
  available: boolean;
  mic: 'on' | 'off' | null;
  camera: 'on' | 'off' | null;
  presenting: boolean;
  canPresent: boolean;
}

export type MeetControlAction = 'toggle-mic' | 'toggle-camera' | 'present';

type ExtensionMessage =
  | { type: 'tab-video-offer'; sdp: string; sessionId: string | null }
  | { type: 'tab-video-ended' }
  | { type: 'meet-controls'; state: MeetControlsState };

interface ExtensionChannelHandlers {
  onStream: (stream: MediaStream) => void;
  onMeetControls: (state: MeetControlsState) => void;
}

const RECONNECT_DELAY_MS = 1000;
// No ICE servers: both ends run in this browser.
const ICE_GATHERING_TIMEOUT_MS = 2000;

function iceGatheringComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(done, ICE_GATHERING_TIMEOUT_MS);
    function done() {
      clearTimeout(timer);
      pc.removeEventListener('icegatheringstatechange', check);
      resolve();
    }
    function check() {
      if (pc.iceGatheringState === 'complete') done();
    }
    pc.addEventListener('icegatheringstatechange', check);
  });
}

function isLive(pc: RTCPeerConnection | null): boolean {
  return !!pc && pc.connectionState !== 'failed' && pc.connectionState !== 'closed';
}

export function useExtensionChannel(
  sessionId: string | null,
  handlers: ExtensionChannelHandlers,
): { sendMeetControl: (action: MeetControlAction) => void } {
  // The latest callbacks, so the port isn't reopened on every render.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const portRef = useRef<ExtensionPort | null>(null);

  useEffect(() => {
    const extensionId = import.meta.env.VITE_EXTENSION_ID;
    const runtime = (globalThis as { chrome?: { runtime?: ChromeRuntime } }).chrome?.runtime;
    if (!sessionId || !extensionId || typeof runtime?.connect !== 'function') return;
    const connect = runtime.connect;

    const viewerId = crypto.randomUUID();
    let pc: RTCPeerConnection | null = null;
    let port: ExtensionPort | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    // Closing the connection ends its track, which clears the overlay.
    const closePeer = () => {
      pc?.close();
      pc = null;
    };

    const answer = async (offerSdp: string, via: ExtensionPort) => {
      closePeer();
      const peer = new RTCPeerConnection();
      pc = peer;
      peer.addEventListener('track', (event) => {
        handlersRef.current.onStream(event.streams[0] ?? new MediaStream([event.track]));
      });
      peer.addEventListener('connectionstatechange', () => {
        if (peer.connectionState === 'failed' && pc === peer) closePeer();
      });
      await peer.setRemoteDescription({ type: 'offer', sdp: offerSdp });
      await peer.setLocalDescription(await peer.createAnswer());
      await iceGatheringComplete(peer);
      via.postMessage({ type: 'tab-video-answer', viewerId, sdp: peer.localDescription?.sdp });
    };

    const open = () => {
      const current = connect(extensionId, { name: 'dashboard' });
      port = current;
      portRef.current = current;
      current.onMessage.addListener((raw) => {
        const message = raw as ExtensionMessage;
        if (message.type === 'tab-video-offer' && message.sessionId === sessionId) {
          answer(message.sdp, current).catch((err) =>
            console.error('[ExtensionVideo] Could not open the meeting video:', err),
          );
        } else if (message.type === 'tab-video-ended') {
          closePeer();
        } else if (message.type === 'meet-controls') {
          handlersRef.current.onMeetControls(message.state);
        }
      });
      // The extension's service worker can stop at any time; reconnecting wakes it.
      // A live video connection runs page to page and doesn't need the port.
      current.onDisconnect.addListener(() => {
        if (port === current) port = null;
        if (portRef.current === current) portRef.current = null;
        if (!closed) retry = setTimeout(open, RECONNECT_DELAY_MS);
      });
      current.postMessage({ type: 'hello', viewerId, sessionId, wantsVideo: !isLive(pc) });
    };

    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      port?.disconnect();
      portRef.current = null;
      closePeer();
    };
  }, [sessionId]);

  const sendMeetControl = useCallback((action: MeetControlAction) => {
    // No port (no extension, or it is reconnecting): the controls show as unavailable anyway.
    portRef.current?.postMessage({ type: 'meet-control', action });
  }, []);

  return { sendMeetControl };
}
