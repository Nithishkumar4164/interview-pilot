import { useEffect, useState } from 'react';

interface ExtensionState {
  sessionId: string | null;
  frontendUrl: string;
  connectionState: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
}

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

export function SidePanel() {
  const [state, setState] = useState<ExtensionState>({
    sessionId: null,
    frontendUrl: 'http://localhost:5173',
    connectionState: 'disconnected',
  });

  useEffect(() => {
    // Fetch initial state from service worker
    chrome.runtime.sendMessage({ type: 'get-state' }, (response) => {
      if (response) {
        setState({
          sessionId: response.sessionId,
          frontendUrl: response.frontendUrl || 'http://localhost:5173',
          connectionState: response.connectionState,
        });
      }
    });

    // Listen for state updates
    const listener = (message: any) => {
      if (message.type === 'state-update') {
        setState({
          sessionId: message.state.sessionId,
          frontendUrl: message.state.frontendUrl || 'http://localhost:5173',
          connectionState: message.state.connectionState,
        });
      }
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, []);

  const isActive = state.sessionId && state.connectionState === 'connected';

  if (isActive) {
    const dashboardUrl = `${state.frontendUrl}/session/${state.sessionId}?panel=1`;
    return (
      <iframe
        src={dashboardUrl}
        style={{
          width: '100%',
          height: '100vh',
          border: 'none',
          display: 'block',
        }}
        allow="clipboard-write"
      />
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.icon}>IP</div>
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

      <p style={styles.message}>
        {state.connectionState === 'disconnected'
          ? 'No active session. Open the extension popup to connect.'
          : state.connectionState === 'connected' && !state.sessionId
            ? 'Connected but no session active.'
            : 'Connecting to session...'}
      </p>

      <p style={styles.hint}>
        Click the InterviewPilot icon in the toolbar to configure and connect.
      </p>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100vh',
    padding: 24,
    fontFamily: 'system-ui, -apple-system, sans-serif',
    color: '#e4e6ed',
    background: '#0f1117',
    textAlign: 'center',
  },
  icon: {
    width: 56,
    height: 56,
    borderRadius: 12,
    background: '#6366f1',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 20,
    fontWeight: 700,
    color: '#fff',
    marginBottom: 16,
  },
  title: {
    fontSize: 18,
    fontWeight: 600,
    margin: '0 0 12px',
  },
  statusBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    marginBottom: 16,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    display: 'inline-block',
  },
  statusText: {
    fontSize: 13,
    color: '#9399b0',
  },
  message: {
    fontSize: 14,
    color: '#9399b0',
    lineHeight: 1.5,
    margin: '0 0 8px',
  },
  hint: {
    fontSize: 12,
    color: '#6b7194',
    lineHeight: 1.5,
    margin: 0,
  },
};
