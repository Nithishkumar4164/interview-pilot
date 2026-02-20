/**
 * Hint state store.
 *
 * Manages progressive hint state, auto-advance countdown, and history.
 */
import { create } from 'zustand';

export type HintLevel = 1 | 2 | 3;
export type HintResponseQuality = 'none' | 'partial' | 'full';

export interface HintEntry {
  id: string;
  questionId: string;
  level: HintLevel;
  text: string;
  displayedAt: string;
  interviewerUsed: boolean;
  candidateResponseQuality: HintResponseQuality;
  timeToRespondMs: number | null;
}

interface HintState {
  currentLevel: number;
  activeHint: HintEntry | null;
  history: HintEntry[];
  autoAdvanceAt: string | null;
  hintSignal: string; // none | nudge | guide | direct

  // Actions
  setActiveHint: (hint: HintEntry) => void;
  acknowledgeHint: (id: string, quality: HintResponseQuality) => void;
  setHintSignal: (signal: string) => void;
  setAutoAdvanceAt: (at: string | null) => void;
  clear: () => void;
}

export const useHintStore = create<HintState>((set) => ({
  currentLevel: 0,
  activeHint: null,
  history: [],
  autoAdvanceAt: null,
  hintSignal: 'none',

  setActiveHint: (hint) =>
    set((s) => ({
      activeHint: hint,
      currentLevel: hint.level,
      history: [...s.history, hint],
      autoAdvanceAt: null,
    })),

  acknowledgeHint: (id, quality) =>
    set((s) => ({
      history: s.history.map((h) =>
        h.id === id
          ? { ...h, interviewerUsed: true, candidateResponseQuality: quality }
          : h,
      ),
      activeHint: s.activeHint?.id === id
        ? { ...s.activeHint, interviewerUsed: true, candidateResponseQuality: quality }
        : s.activeHint,
    })),

  setHintSignal: (signal) => set({ hintSignal: signal }),

  setAutoAdvanceAt: (at) => set({ autoAdvanceAt: at }),

  clear: () =>
    set({
      currentLevel: 0,
      activeHint: null,
      history: [],
      autoAdvanceAt: null,
      hintSignal: 'none',
    }),
}));
