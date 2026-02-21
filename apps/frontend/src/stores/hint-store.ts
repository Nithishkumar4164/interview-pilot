/**
 * Hint state store.
 *
 * Manages progressive hint state, auto-advance countdown, history,
 * and runtime (LLM-generated) hints.
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

// ── Runtime Hint Types ───────────────────────────────────────────────

export type RuntimeHintDirectness = 'subtle' | 'moderate' | 'direct';
export type RegenerationReason = 'too_direct' | 'not_useful' | 'off_topic' | 'other';

export interface RuntimeHintEntry {
  hintId: string;
  questionId: string;
  text: string;
  directness: RuntimeHintDirectness;
  isRegeneration: boolean;
  regenerationReason: RegenerationReason | null;
  generationNumber: number;
  latencyMs: number;
  provider: string;
  timestamp: string;
  feedback: 'well_received' | 'partially_received' | 'not_understood' | null;
}

interface HintState {
  currentLevel: number;
  activeHint: HintEntry | null;
  history: HintEntry[];
  givenLevels: Set<number>;
  autoAdvanceAt: string | null;
  hintSignal: string; // none | nudge | guide | direct

  // Runtime hints
  activeRuntimeHint: RuntimeHintEntry | null;
  runtimeHintHistory: RuntimeHintEntry[];
  runtimeHintLoading: boolean;

  // Actions
  setActiveHint: (hint: HintEntry) => void;
  acknowledgeHint: (id: string, quality: HintResponseQuality) => void;
  setHintSignal: (signal: string) => void;
  setAutoAdvanceAt: (at: string | null) => void;
  setRuntimeHintLoading: (loading: boolean) => void;
  setActiveRuntimeHint: (hint: RuntimeHintEntry) => void;
  acknowledgeRuntimeHint: (hintId: string, feedback: RuntimeHintEntry['feedback']) => void;
  clear: () => void;
}

export const useHintStore = create<HintState>((set) => ({
  currentLevel: 0,
  activeHint: null,
  history: [],
  givenLevels: new Set<number>(),
  autoAdvanceAt: null,
  hintSignal: 'none',

  // Runtime hints
  activeRuntimeHint: null,
  runtimeHintHistory: [],
  runtimeHintLoading: false,

  setActiveHint: (hint) =>
    set((s) => {
      const givenLevels = new Set(s.givenLevels);
      givenLevels.add(hint.level);
      return {
        activeHint: hint,
        currentLevel: hint.level,
        history: [...s.history, hint],
        givenLevels,
        autoAdvanceAt: null,
      };
    }),

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

  setRuntimeHintLoading: (loading) => set({ runtimeHintLoading: loading }),

  setActiveRuntimeHint: (hint) =>
    set((s) => ({
      activeRuntimeHint: hint,
      runtimeHintHistory: [...s.runtimeHintHistory, hint],
      runtimeHintLoading: false,
    })),

  acknowledgeRuntimeHint: (hintId, feedback) =>
    set((s) => ({
      runtimeHintHistory: s.runtimeHintHistory.map((h) =>
        h.hintId === hintId ? { ...h, feedback } : h,
      ),
      activeRuntimeHint:
        s.activeRuntimeHint?.hintId === hintId
          ? { ...s.activeRuntimeHint, feedback }
          : s.activeRuntimeHint,
    })),

  clear: () =>
    set({
      currentLevel: 0,
      activeHint: null,
      history: [],
      givenLevels: new Set<number>(),
      autoAdvanceAt: null,
      hintSignal: 'none',
      activeRuntimeHint: null,
      runtimeHintHistory: [],
      runtimeHintLoading: false,
    }),
}));
