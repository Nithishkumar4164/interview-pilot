import React, { useCallback, useEffect, useState } from 'react';

interface ExtensionState {
  sessionId: string | null;
  backendUrl: string;
  connectionState: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
  tabCaptureActive: boolean;
  micCaptureActive: boolean;
  codeScrapingActive: boolean;
  bufferStats: {
    audioChunks: number;
    codeSnapshots: number;
    estimatedDurationMs: number;
  };
}

const DEFAULT_STATE: ExtensionState = {
  sessionId: null,
  backendUrl: 'ws://localhost:8000',
  connectionState: 'disconnected',
  tabCaptureActive: false,
  micCaptureActive: false,
  codeScrapingActive: false,
  bufferStats: { audioChunks: 0, codeSnapshots: 0, estimatedDurationMs: 0 },
};

const STATUS_COLORS: Record<string, string> = {
  disconnected: '#94a3b8',
  connecting: '#f59e0b',
  connected: '#22c55e',
  reconnecting: '#f59e0b',
};

const STATUS_LABELS: Record<string, string> = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  connected: 'Connected',
  reconnecting: 'Reconnecting...',
};

export function Popup() {
  const [state, setState] = useState<ExtensionState>(DEFAULT_STATE);
  const [sessionId, setSessionId] = useState('');
  const [backendUrl, setBackendUrl] = useState('ws://localhost:8000');
  const [frontendUrl, setFrontendUrl] = useState('http://localhost:5173');

  // Fetch initial state from service worker
  useEffect(() => {
    chrome.runtime.sendMessage({ type: 'get-state' }, (response) => {
      if (response) {
        setState(response);
        if (response.sessionId) setSessionId(response.sessionId);
        if (response.backendUrl) setBackendUrl(response.backendUrl);
        if (response.frontendUrl) setFrontendUrl(response.frontendUrl);
      }
    });

    // Listen for state updates
    const listener = (message: any) => {
      if (message.type === 'state-update') {
        setState(message.state);
      }
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, []);

  const handleConnect = useCallback(() => {
    if (!sessionId.trim()) return;
    chrome.runtime.sendMessage({
      type: 'connect',
      config: { sessionId: sessionId.trim(), backendUrl, frontendUrl },
    });
  }, [sessionId, backendUrl, frontendUrl]);

  const handleDisconnect = useCallback(() => {
    chrome.runtime.sendMessage({ type: 'disconnect' });
  }, []);

  const handleToggleTabCapture = useCallback(async () => {
    if (state.tabCaptureActive) {
      chrome.runtime.sendMessage({ type: 'stop-tab-capture' });
      return;
    }
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id) return;
      // getMediaStreamId may be callback-based — wrap to ensure promise
      const streamId = await new Promise<string>((resolve, reject) => {
        (chrome.tabCapture as any).getMediaStreamId(
          { targetTabId: tab.id },
          (id: string) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
            } else {
              resolve(id);
            }
          },
        );
      });
      chrome.runtime.sendMessage({ type: 'start-tab-capture', streamId });
    } catch (err) {
      console.error('[Popup] Tab capture failed:', err);
    }
  }, [state.tabCaptureActive]);

  const handleToggleMicCapture = useCallback(() => {
    if (state.micCaptureActive) {
      chrome.runtime.sendMessage({ type: 'stop-mic-capture' });
    } else {
      chrome.runtime.sendMessage({ type: 'start-mic-capture' });
    }
  }, [state.micCaptureActive]);

  const isConnected = state.connectionState === 'connected';
  const hasBuffer = state.bufferStats.audioChunks > 0 || state.bufferStats.codeSnapshots > 0;
  const bufferDurationSec = Math.round(state.bufferStats.estimatedDurationMs / 1000);

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <h2 style={styles.title}>InterviewPilot</h2>
        <div style={styles.statusBadge}>
          <span
            style={{
              ...styles.statusDot,
              backgroundColor: STATUS_COLORS[state.connectionState],
            }}
          />
          <span style={styles.statusText}>
            {STATUS_LABELS[state.connectionState]}
          </span>
        </div>
      </div>

      {/* Connection Config */}
      {state.connectionState === 'disconnected' && (
        <div style={styles.section}>
          <label style={styles.label}>Session ID</label>
          <input
            type="text"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            placeholder="Enter session ID"
            style={styles.input}
          />
          <label style={styles.label}>Backend URL</label>
          <input
            type="text"
            value={backendUrl}
            onChange={(e) => setBackendUrl(e.target.value)}
            style={styles.input}
          />
          <label style={styles.label}>Dashboard URL</label>
          <input
            type="text"
            value={frontendUrl}
            onChange={(e) => setFrontendUrl(e.target.value)}
            style={styles.input}
          />
          <button
            onClick={handleConnect}
            disabled={!sessionId.trim()}
            style={{
              ...styles.button,
              ...styles.buttonPrimary,
              opacity: sessionId.trim() ? 1 : 0.5,
            }}
          >
            Connect
          </button>
        </div>
      )}

      {/* Connected Controls */}
      {state.connectionState !== 'disconnected' && (
        <>
          <div style={styles.section}>
            <div style={styles.sessionInfo}>
              Session: <strong>{state.sessionId}</strong>
            </div>
          </div>

          {/* Capture Toggles */}
          <div style={styles.section}>
            <div style={styles.sectionTitle}>Capture</div>
            <button
              onClick={handleToggleTabCapture}
              disabled={state.connectionState !== 'connected'}
              style={{
                ...styles.button,
                ...styles.captureButton,
                backgroundColor: state.tabCaptureActive ? '#22c55e' : '#3b82f6',
                opacity: state.connectionState !== 'connected' ? 0.5 : 1,
                marginBottom: 6,
              }}
            >
              {state.tabCaptureActive ? 'Stop Tab Audio' : 'Start Tab Audio'}
            </button>
            <button
              onClick={handleToggleMicCapture}
              disabled={state.connectionState !== 'connected'}
              style={{
                ...styles.button,
                ...styles.captureButton,
                backgroundColor: state.micCaptureActive ? '#22c55e' : '#3b82f6',
                opacity: state.connectionState !== 'connected' ? 0.5 : 1,
                marginBottom: 6,
              }}
            >
              {state.micCaptureActive ? 'Stop Microphone' : 'Start Microphone'}
            </button>
            <div style={styles.toggleRow}>
              <span style={styles.toggleLabel}>Code Scraping</span>
              <span
                style={{
                  ...styles.toggleIndicator,
                  backgroundColor: state.codeScrapingActive ? '#22c55e' : '#94a3b8',
                }}
              >
                {state.codeScrapingActive ? 'ON' : 'OFF'}
              </span>
            </div>
          </div>

          {/* Buffer Status */}
          {hasBuffer && (
            <div style={styles.section}>
              <div style={styles.sectionTitle}>Offline Buffer</div>
              <div style={styles.bufferInfo}>
                <span>{state.bufferStats.audioChunks} audio chunks</span>
                {bufferDurationSec > 0 && <span> ({bufferDurationSec}s)</span>}
              </div>
              <div style={styles.bufferInfo}>
                <span>{state.bufferStats.codeSnapshots} code snapshots</span>
              </div>
            </div>
          )}

          {/* Disconnect */}
          <div style={styles.section}>
            <button
              onClick={handleDisconnect}
              style={{ ...styles.button, ...styles.buttonDanger }}
            >
              Disconnect
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: 320,
    padding: 16,
    fontFamily: 'system-ui, -apple-system, sans-serif',
    fontSize: 13,
    color: '#1e293b',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
    paddingBottom: 8,
    borderBottom: '1px solid #e2e8f0',
  },
  title: {
    margin: 0,
    fontSize: 16,
    fontWeight: 600,
  },
  statusBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    display: 'inline-block',
  },
  statusText: {
    fontSize: 12,
    color: '#64748b',
  },
  section: {
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    color: '#64748b',
    letterSpacing: '0.05em',
    marginBottom: 6,
  },
  label: {
    display: 'block',
    fontSize: 12,
    color: '#64748b',
    marginBottom: 4,
  },
  input: {
    width: '100%',
    padding: '6px 8px',
    border: '1px solid #cbd5e1',
    borderRadius: 4,
    fontSize: 13,
    marginBottom: 8,
    boxSizing: 'border-box' as const,
    outline: 'none',
  },
  button: {
    width: '100%',
    padding: '8px 12px',
    border: 'none',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
  },
  buttonPrimary: {
    backgroundColor: '#3b82f6',
    color: 'white',
  },
  buttonDanger: {
    backgroundColor: '#ef4444',
    color: 'white',
  },
  captureButton: {
    color: 'white',
  },
  sessionInfo: {
    fontSize: 12,
    color: '#475569',
  },
  toggleRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '4px 0',
  },
  toggleLabel: {
    fontSize: 13,
  },
  toggleIndicator: {
    fontSize: 10,
    fontWeight: 600,
    color: 'white',
    padding: '2px 8px',
    borderRadius: 10,
  },
  bufferInfo: {
    fontSize: 12,
    color: '#64748b',
    padding: '2px 0',
  },
};
