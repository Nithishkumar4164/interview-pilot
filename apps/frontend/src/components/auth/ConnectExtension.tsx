/**
 * Hands the Chrome extension a credential for this one session, and the
 * session's Google Meet link.
 *
 * With VITE_EXTENSION_ID set, the pairing token is sent straight to the
 * extension (it lists this origin under externally_connectable), along with
 * the Meet link: the extension opens that tab and tracks it for the session.
 * Otherwise the token is shown as a pairing code to paste into the extension
 * popup.
 *
 * Once the extension reports it is paired with this session, the form gives
 * way to its status (waiting for capture, or capturing), with Reconnect to
 * pair again or change the Meet link.
 */
import { useEffect, useState } from 'react';
import * as api from '@/api/client';
import { MEETING_URL_HINT, normalizeMeetingUrl } from '@/lib/meeting';
import type { ExtensionStatus } from '@/hooks/useExtensionChannel';

interface ChromeRuntime {
  sendMessage: (extensionId: string, message: unknown, callback: (response: unknown) => void) => void;
  lastError?: { message?: string };
}

function chromeRuntime(): ChromeRuntime | null {
  const runtime = (globalThis as { chrome?: { runtime?: ChromeRuntime } }).chrome?.runtime;
  return runtime && typeof runtime.sendMessage === 'function' ? runtime : null;
}

function sendToExtension(extensionId: string, message: unknown): Promise<boolean> {
  const runtime = chromeRuntime();
  if (!runtime) return Promise.resolve(false);
  return new Promise((resolve) => {
    try {
      runtime.sendMessage(extensionId, message, (response) => {
        resolve(!runtime.lastError && (response as { ok?: boolean } | undefined)?.ok === true);
      });
    } catch {
      resolve(false);
    }
  });
}

type State =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'sent'; openedMeeting: boolean }
  | { kind: 'code'; code: string; copied: boolean }
  | { kind: 'error'; message: string };

interface ConnectExtensionProps {
  sessionId: string;
  /** What the extension last reported; null before it has (or without one). */
  status?: ExtensionStatus | null;
}

const START_CAPTURE_HINT = 'On the Meet tab, press Alt+Shift+I (Option+Shift+I on Mac) to start capture.';

export function ConnectExtension({ sessionId, status = null }: ConnectExtensionProps) {
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [reconnecting, setReconnecting] = useState(false);
  // The saved link, and what is in the field.
  const [savedMeetingUrl, setSavedMeetingUrl] = useState<string | null>(null);
  const [meetingUrl, setMeetingUrl] = useState('');

  useEffect(() => {
    let cancelled = false;
    api
      .getSession(sessionId)
      .then((session) => {
        const saved = typeof session.meeting_url === 'string' ? session.meeting_url : null;
        if (cancelled) return;
        setSavedMeetingUrl(saved);
        setMeetingUrl(saved ?? '');
      })
      .catch(() => {
        // The field stays empty; the link can still be typed in.
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  /** Save the field if it changed; the link to hand the extension, or undefined if it isn't valid. */
  const saveMeetingUrl = async (): Promise<string | null | undefined> => {
    const normalized = normalizeMeetingUrl(meetingUrl);
    if (normalized === undefined) return undefined;
    if (normalized !== savedMeetingUrl) {
      const { meeting_url } = await api.updateMeetingUrl(sessionId, normalized);
      setSavedMeetingUrl(meeting_url);
    }
    setMeetingUrl(normalized ?? '');
    return normalized;
  };

  const connect = async () => {
    setState({ kind: 'working' });
    try {
      const link = await saveMeetingUrl();
      if (link === undefined) {
        setState({ kind: 'error', message: MEETING_URL_HINT });
        return;
      }
      const { pairing_token } = await api.createPairingToken(sessionId);
      const extensionId = import.meta.env.VITE_EXTENSION_ID;
      if (extensionId) {
        const wsBase = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
        const delivered = await sendToExtension(extensionId, {
          type: 'pair',
          pairingToken: pairing_token,
          backendUrl: wsBase,
          frontendUrl: location.origin,
          meetingUrl: link,
        });
        if (delivered) {
          setReconnecting(false);
          setState({ kind: 'sent', openedMeeting: !!link });
          return;
        }
      }
      setState({ kind: 'code', code: pairing_token, copied: false });
    } catch (err) {
      setState({ kind: 'error', message: err instanceof Error ? err.message : 'Could not create a pairing code' });
    }
  };

  if (status?.sessionId === sessionId && !reconnecting) {
    return (
      <div className="connect-extension" role="status">
        <span
          className={`connect-extension__dot${status.capturing ? ' connect-extension__dot--live' : ''}`}
          aria-hidden="true"
        />
        <span className="connect-extension__status">
          {status.capturing ? 'Extension is capturing the meeting.' : `Extension connected. ${START_CAPTURE_HINT}`}
          {!status.connected && ' Reconnecting to the server…'}
        </span>
        <button
          className="btn btn--ghost btn--small"
          onClick={() => {
            setState({ kind: 'idle' });
            setReconnecting(true);
          }}
        >
          Reconnect
        </button>
      </div>
    );
  }

  return (
    <div className="connect-extension">
      <input
        className="connect-extension__meeting"
        type="url"
        placeholder="Google Meet link (optional)"
        aria-label="Google Meet link"
        value={meetingUrl}
        onChange={(e) => setMeetingUrl(e.target.value)}
      />
      <button className="btn btn--secondary btn--small" onClick={connect} disabled={state.kind === 'working'}>
        {state.kind === 'working' ? 'Connecting…' : 'Connect extension'}
      </button>
      {state.kind === 'sent' && (
        <span className="connect-extension__status">
          {state.openedMeeting ? `Extension connected. ${START_CAPTURE_HINT}` : 'Extension connected to this session.'}
        </span>
      )}
      {state.kind === 'error' && (
        <span className="connect-extension__status connect-extension__status--error" role="alert">
          {state.message}
        </span>
      )}
      {state.kind === 'code' && (
        <div className="connect-extension__code">
          <span className="connect-extension__status">
            Paste this pairing code into the extension popup. It only works for this session.
          </span>
          <input
            className="connect-extension__input"
            readOnly
            value={state.code}
            aria-label="Pairing code"
            onFocus={(e) => e.currentTarget.select()}
          />
          <button
            className="btn btn--ghost btn--small"
            onClick={async () => {
              await navigator.clipboard.writeText(state.code);
              setState({ ...state, copied: true });
            }}
          >
            {state.copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      )}
    </div>
  );
}
