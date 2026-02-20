/**
 * Offline mode & connection status store.
 *
 * Tracks WebSocket connectivity, backend availability,
 * and manages 3-level degradation.
 */
import { create } from 'zustand';

export type ConnectionStatus = 'connected' | 'reconnecting' | 'disconnected';

/** Degradation levels for offline mode:
 * 0 = fully connected
 * 1 = LLM down (transcript continues, cached hints, manual scoring)
 * 2 = backend down (extension buffers, local timer, manual notes)
 * 3 = fully offline
 */
export type DegradationLevel = 0 | 1 | 2 | 3;

interface OfflineState {
  wsStatus: ConnectionStatus;
  backendStatus: ConnectionStatus;
  degradationLevel: DegradationLevel;
  reconnectAttempts: number;
  lastConnectedAt: string | null;
  providerStatuses: Record<string, string>;
  manualNotes: string;

  // Actions
  setWSStatus: (status: ConnectionStatus) => void;
  setBackendStatus: (status: ConnectionStatus) => void;
  setDegradationLevel: (level: DegradationLevel) => void;
  setReconnectAttempts: (n: number) => void;
  setLastConnected: () => void;
  setProviderStatus: (provider: string, status: string) => void;
  setManualNotes: (notes: string) => void;
  reset: () => void;
}

export const useOfflineStore = create<OfflineState>((set) => ({
  wsStatus: 'disconnected',
  backendStatus: 'disconnected',
  degradationLevel: 0,
  reconnectAttempts: 0,
  lastConnectedAt: null,
  providerStatuses: {},
  manualNotes: '',

  setWSStatus: (status) =>
    set((s) => {
      const degradation = computeDegradation(status, s.backendStatus);
      return {
        wsStatus: status,
        degradationLevel: degradation,
        lastConnectedAt: status === 'connected' ? new Date().toISOString() : s.lastConnectedAt,
      };
    }),

  setBackendStatus: (status) =>
    set((s) => ({
      backendStatus: status,
      degradationLevel: computeDegradation(s.wsStatus, status),
    })),

  setDegradationLevel: (level) => set({ degradationLevel: level }),

  setReconnectAttempts: (n) => set({ reconnectAttempts: n }),

  setLastConnected: () => set({ lastConnectedAt: new Date().toISOString() }),

  setProviderStatus: (provider, status) =>
    set((s) => ({
      providerStatuses: { ...s.providerStatuses, [provider]: status },
    })),

  setManualNotes: (notes) => set({ manualNotes: notes }),

  reset: () =>
    set({
      wsStatus: 'disconnected',
      backendStatus: 'disconnected',
      degradationLevel: 0,
      reconnectAttempts: 0,
      lastConnectedAt: null,
      providerStatuses: {},
      manualNotes: '',
    }),
}));

function computeDegradation(
  ws: ConnectionStatus,
  backend: ConnectionStatus,
): DegradationLevel {
  if (ws === 'connected' && backend === 'connected') return 0;
  if (ws === 'connected' && backend !== 'connected') return 1;
  if (ws !== 'connected' && backend === 'connected') return 2;
  return 3;
}
