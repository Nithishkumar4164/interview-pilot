/**
 * Session state store.
 *
 * Manages current interview session state, timer, and phase tracking.
 */
import { create } from 'zustand';
import { usePlanStore } from './plan-store';
import type { PlannedQuestion, PlanPhase } from './plan-store';

export type SessionStatus = 'idle' | 'planning' | 'ready' | 'live' | 'paused' | 'ended';

/** Flatten all plan questions into a single ordered list with phase metadata. */
export interface FlatQuestion extends PlannedQuestion {
  phaseIndex: number;
  phaseName: string;
}

export function getAllPlanQuestions(): FlatQuestion[] {
  const plan = usePlanStore.getState().plan;
  if (!plan) return [];
  return plan.phases.flatMap((phase: PlanPhase, pi: number) =>
    phase.questions.map((q) => ({ ...q, phaseIndex: pi, phaseName: phase.phase })),
  );
}

export function getPhaseIndexForQuestion(questionIndex: number): number {
  const plan = usePlanStore.getState().plan;
  if (!plan) return 0;
  let count = 0;
  for (let i = 0; i < plan.phases.length; i++) {
    count += plan.phases[i].questions.length;
    if (questionIndex < count) return i;
  }
  return 0;
}

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
  currentPhaseIndex: number;
  questionStartedAtMs: number;
  contextReloading: boolean;

  // Actions
  setSession: (id: string, level: string, duration: number) => void;
  setStatus: (status: SessionStatus) => void;
  setStarted: (at: string) => void;
  tickElapsed: (deltaMs: number) => void;
  setCurrentQuestion: (index: number, total: number, text: string) => void;
  advanceQuestion: (delta: number) => void;
  goToQuestion: (index: number) => void;
  resetQuestionTimer: () => void;
  setContextReloading: (loading: boolean) => void;
  restoreFromServer: (data: {
    sessionId: string;
    status: SessionStatus;
    level: string;
    durationMinutes: number;
    elapsedMs: number;
    startedAt: string | null;
    currentQuestionIndex?: number;
  }) => void;
  reset: () => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessionId: null,
  status: 'idle',
  level: '',
  durationMinutes: 45,
  elapsedMs: 0,
  startedAt: null,
  currentQuestionIndex: 0,
  totalQuestions: 0,
  currentQuestionText: '',
  currentPhaseIndex: 0,
  questionStartedAtMs: 0,
  contextReloading: false,

  setSession: (id, level, duration) =>
    set({ sessionId: id, level, durationMinutes: duration, status: 'ready' }),

  setStatus: (status) => set({ status }),

  setStarted: (at) => set({ startedAt: at, status: 'live' }),

  tickElapsed: (deltaMs) =>
    set((s) => ({ elapsedMs: s.elapsedMs + deltaMs })),

  setCurrentQuestion: (index, total, text) =>
    set({
      currentQuestionIndex: index,
      totalQuestions: total,
      currentQuestionText: text,
      currentPhaseIndex: getPhaseIndexForQuestion(index),
      questionStartedAtMs: get().elapsedMs,
    }),

  advanceQuestion: (delta) => {
    const { currentQuestionIndex, elapsedMs } = get();
    const allQ = getAllPlanQuestions();
    const newIndex = currentQuestionIndex + delta;
    if (newIndex < 0 || newIndex >= allQ.length) return;
    const q = allQ[newIndex];
    if (!q) return;
    set({
      currentQuestionIndex: newIndex,
      totalQuestions: allQ.length,
      currentQuestionText: q.text,
      currentPhaseIndex: q.phaseIndex,
      questionStartedAtMs: elapsedMs,
    });
  },

  goToQuestion: (index) => {
    const { elapsedMs } = get();
    const allQ = getAllPlanQuestions();
    if (index < 0 || index >= allQ.length) return;
    const q = allQ[index];
    if (!q) return;
    set({
      currentQuestionIndex: index,
      totalQuestions: allQ.length,
      currentQuestionText: q.text,
      currentPhaseIndex: q.phaseIndex,
      questionStartedAtMs: elapsedMs,
    });
  },

  resetQuestionTimer: () => set({ questionStartedAtMs: get().elapsedMs }),

  setContextReloading: (loading) => set({ contextReloading: loading }),

  restoreFromServer: (data) => {
    const allQ = getAllPlanQuestions();
    const questionIndex = data.currentQuestionIndex ?? 0;
    const q = allQ[questionIndex];
    set({
      sessionId: data.sessionId,
      status: data.status,
      level: data.level,
      durationMinutes: data.durationMinutes,
      elapsedMs: data.elapsedMs,
      startedAt: data.startedAt,
      currentQuestionIndex: questionIndex,
      totalQuestions: allQ.length,
      currentQuestionText: q?.text ?? '',
      currentPhaseIndex: q ? getPhaseIndexForQuestion(questionIndex) : 0,
      questionStartedAtMs: data.elapsedMs,
    });
  },

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
      currentPhaseIndex: 0,
      questionStartedAtMs: 0,
      contextReloading: false,
    }),
}));
