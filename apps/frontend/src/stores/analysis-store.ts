/**
 * Real-time analysis store.
 *
 * Stores analysis events, score updates, red flags, and code analysis.
 */
import { create } from 'zustand';

export interface AnalysisEntry {
  answerQuality: number;
  onTrack: boolean;
  insight: string;
  redFlags: string[];
  suggestionForInterviewer: string;
  hintSignal: string;
  timestamp: string;
  provider: string;
}

export interface CodeAnalysisState {
  codeLength: number;
  language: string;
  source: string;
  progressState: string;
  detectedApproach: { name: string; confidence: number } | null;
  isStuck: boolean;
  bugs: string[];
  timestamp: string;
}

export interface ScoreState {
  problemSolving: number;
  codeQuality: number;
  communication: number;
  hintResponsiveness: number;
}

interface AnalysisStoreState {
  analyses: AnalysisEntry[];
  latestAnalysis: AnalysisEntry | null;
  redFlags: Array<{ text: string; timestamp: string; type?: string }>;
  codeAnalysis: CodeAnalysisState | null;
  scores: ScoreState;

  // Actions
  addAnalysis: (entry: AnalysisEntry) => void;
  addRedFlag: (flag: { text: string; timestamp: string; type?: string }) => void;
  setCodeAnalysis: (state: CodeAnalysisState) => void;
  updateScores: (scores: Partial<ScoreState>) => void;
  clear: () => void;
}

const DEFAULT_SCORES: ScoreState = {
  problemSolving: 0,
  codeQuality: 0,
  communication: 0,
  hintResponsiveness: 0,
};

export const useAnalysisStore = create<AnalysisStoreState>((set) => ({
  analyses: [],
  latestAnalysis: null,
  redFlags: [],
  codeAnalysis: null,
  scores: { ...DEFAULT_SCORES },

  addAnalysis: (entry) =>
    set((s) => {
      const analyses = [...s.analyses, entry].slice(-50);
      // Update running score average
      const scored = analyses.filter((a) => a.answerQuality > 0);
      const avg = scored.length > 0
        ? scored.reduce((sum, a) => sum + a.answerQuality, 0) / scored.length
        : 0;

      return {
        analyses,
        latestAnalysis: entry,
        scores: {
          ...s.scores,
          problemSolving: Math.round(avg * 10) / 10,
        },
      };
    }),

  addRedFlag: (flag) =>
    set((s) => ({ redFlags: [...s.redFlags, flag].slice(-20) })),

  setCodeAnalysis: (state) => set({ codeAnalysis: state }),

  updateScores: (scores) =>
    set((s) => ({ scores: { ...s.scores, ...scores } })),

  clear: () =>
    set({
      analyses: [],
      latestAnalysis: null,
      redFlags: [],
      codeAnalysis: null,
      scores: { ...DEFAULT_SCORES },
    }),
}));
