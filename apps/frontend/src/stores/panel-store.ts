/**
 * Which live-session panes are hidden. A per-viewer convenience kept in
 * localStorage, so the layout survives a reload.
 */
import { create } from 'zustand';

export type PanelId = 'transcript' | 'plan' | 'copilot' | 'speakers' | 'scores' | 'flags' | 'hints';

/** Every pane, in the order the Panes menu lists them. */
export const PANES: ReadonlyArray<{ id: PanelId; label: string }> = [
  { id: 'transcript', label: 'Transcript' },
  { id: 'plan', label: 'Questions' },
  { id: 'copilot', label: 'Copilot' },
  { id: 'speakers', label: 'Speakers' },
  { id: 'scores', label: 'Scores' },
  { id: 'flags', label: 'Flags' },
  { id: 'hints', label: 'Smart Hints' },
];

export function paneLabel(id: PanelId): string {
  return PANES.find((p) => p.id === id)?.label ?? id;
}

const STORAGE_KEY = 'live_panes_hidden';

function loadHidden(): Partial<Record<PanelId, boolean>> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    return Object.fromEntries(Object.entries(parsed).filter(([, v]) => v === true)) as Partial<Record<PanelId, boolean>>;
  } catch {
    return {};
  }
}

function saveHidden(hidden: Partial<Record<PanelId, boolean>>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(hidden));
  } catch {
    // per-viewer convenience only
  }
}

interface PanelState {
  hidden: Partial<Record<PanelId, boolean>>;
  setShown: (id: PanelId, shown: boolean) => void;
  showAll: () => void;
}

export const usePanelStore = create<PanelState>((set) => ({
  hidden: loadHidden(),
  setShown: (id, shown) =>
    set((state) => {
      const hidden = { ...state.hidden };
      if (shown) delete hidden[id];
      else hidden[id] = true;
      saveHidden(hidden);
      return { hidden };
    }),
  showAll: () => {
    saveHidden({});
    set({ hidden: {} });
  },
}));

export function usePaneHidden(id: PanelId): boolean {
  return usePanelStore((s) => !!s.hidden[id]);
}
