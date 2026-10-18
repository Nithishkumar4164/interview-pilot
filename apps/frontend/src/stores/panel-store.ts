/**
 * Which live-session panels are collapsed. A per-viewer convenience kept in
 * localStorage, so the layout survives a reload.
 */
import { create } from 'zustand';

export type PanelId = 'transcript' | 'plan' | 'copilot' | 'speakers' | 'scores' | 'flags' | 'hints';

const STORAGE_KEY = 'live_panels_collapsed';

function loadCollapsed(): Partial<Record<PanelId, boolean>> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    return Object.fromEntries(Object.entries(parsed).filter(([, v]) => v === true)) as Partial<Record<PanelId, boolean>>;
  } catch {
    return {};
  }
}

function saveCollapsed(collapsed: Partial<Record<PanelId, boolean>>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(collapsed));
  } catch {
    // per-viewer convenience only
  }
}

interface PanelState {
  collapsed: Partial<Record<PanelId, boolean>>;
  toggle: (id: PanelId) => void;
}

export const usePanelStore = create<PanelState>((set) => ({
  collapsed: loadCollapsed(),
  toggle: (id) =>
    set((state) => {
      const collapsed = { ...state.collapsed, [id]: !state.collapsed[id] };
      saveCollapsed(collapsed);
      return { collapsed };
    }),
}));

export function usePanelCollapsed(id: PanelId): boolean {
  return usePanelStore((s) => !!s.collapsed[id]);
}
