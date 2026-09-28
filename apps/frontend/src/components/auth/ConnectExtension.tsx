/**
 * Hands the Chrome extension a credential for this one session.
 *
 * With VITE_EXTENSION_ID set, the pairing token is sent straight to the
 * extension (it lists this origin under externally_connectable). Otherwise
 * the token is shown as a pairing code to paste into the extension popup.
 */
import { useState } from 'react';
import * as api from '@/api/client';

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
  | { kind: 'sent' }
  | { kind: 'code'; code: string; copied: boolean }
  | { kind: 'error'; message: string };

export function ConnectExtension({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState<State>({ kind: 'idle' });

  const connect = async () => {
    setState({ kind: 'working' });
    try {
      const { pairing_token } = await api.createPairingToken(sessionId);
      const extensionId = import.meta.env.VITE_EXTENSION_ID;
      if (extensionId) {
        const wsBase = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
        const delivered = await sendToExtension(extensionId, {
          type: 'pair',
          pairingToken: pairing_token,
          backendUrl: wsBase,
          frontendUrl: location.origin,
        });
        if (delivered) {
          setState({ kind: 'sent' });
          return;
        }
      }
      setState({ kind: 'code', code: pairing_token, copied: false });
    } catch (err) {
      setState({ kind: 'error', message: err instanceof Error ? err.message : 'Could not create a pairing code' });
    }
  };

  return (
    <div className="connect-extension">
      <button className="btn btn--secondary btn--small" onClick={connect} disabled={state.kind === 'working'}>
        {state.kind === 'working' ? 'Connecting…' : 'Connect extension'}
      </button>
      {state.kind === 'sent' && <span className="connect-extension__status">Extension connected to this session.</span>}
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
