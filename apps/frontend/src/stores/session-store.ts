/**
 * Session state store.
 *
 * Manages current interview session state, timer, and phase tracking.
 */
import { create } from 'zustand';

export type SessionStatus = 'idle' | 'planning' | 'ready' | 'live' | 'paused' | 'ended';

export interface SessionState {
  sessionId: string | null;
  status: SessionStatus;
  level: string;
  durationMinutes: number;
  elapsedMs: number;
  startedAt: string | null;
  currentQuestionIndex: number;
  totalQuestions: number;
  currentQuestionText: string;

  // Actions
  setSession: (id: string, level: string, duration: number) => void;
  setStatus: (status: SessionStatus) => void;
  setStarted: (at: string) => void;
  tickElapsed: (deltaMs: number) => void;
  setCurrentQuestion: (index: number, total: number, text: string) => void;
  reset: () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionId: null,
  status: 'idle',
  level: '',
  durationMinutes: 45,
  elapsedMs: 0,
  startedAt: null,
  currentQuestionIndex: 0,
  totalQuestions: 0,
  currentQuestionText: '',

  setSession: (id, level, duration) =>
    set({ sessionId: id, level, durationMinutes: duration, status: 'ready' }),

  setStatus: (status) => set({ status }),

  setStarted: (at) => set({ startedAt: at, status: 'live' }),

  tickElapsed: (deltaMs) =>
    set((s) => ({ elapsedMs: s.elapsedMs + deltaMs })),

  setCurrentQuestion: (index, total, text) =>
    set({ currentQuestionIndex: index, totalQuestions: total, currentQuestionText: text }),

  reset: () =>
    set({
      sessionId: null,
      status: 'idle',
      level: '',
      durationMinutes: 45,
      elapsedMs: 0,
      startedAt: null,
      currentQuestionIndex: 0,
      totalQuestions: 0,
      currentQuestionText: '',
    }),
}));
