/**
 * Zustand store for the interview's questions and resume state.
 *
 * Questions are generated during the interview; each one the interviewer uses
 * is logged to the questionnaire. The live dashboard reads the questionnaire as
 * a plan (a phase per run of one category), the shape sessions planned up front
 * still restore with.
 */
import { create } from 'zustand';
import type { LoggedQuestion } from '@/api/client';

// ── Types ──────────────────────────────────────────────────────────────

export interface QuestionHint {
  level: number;
  text: string;
}

export interface ApproachPattern {
  name: string;
  data_structures: string[];
  expected_complexity: Record<string, string>;
  key_steps: string[];
  code_snippet?: string;
}

export interface PlannedQuestion {
  id: string;
  text: string;
  category: string;
  difficulty: number;
  time_minutes: number;
  alternates: string[];
  hints: QuestionHint[];
  brute_force_approach: ApproachPattern | null;
  optimal_approaches: ApproachPattern[] | null;
  common_pitfalls: string[] | null;
}

export interface PlanPhase {
  phase: string;
  start_minute: number;
  duration_minutes: number;
  questions: PlannedQuestion[];
}

export interface InterviewPlan {
  id: string;
  session_id: string;
  version: number;
  is_active: boolean;
  phases: PlanPhase[];
  total_minutes: number;
  created_at: string;
}

export type PlanStatus = 'idle' | 'generating' | 'ready' | 'error';

// ── Resume types ───────────────────────────────────────────────────────

export type ResumeStatus = 'idle' | 'uploading' | 'processing' | 'completed' | 'failed';

export interface ResumeState {
  referenceId: string | null;
  filename: string | null;
  status: ResumeStatus;
  structuredData: Record<string, unknown> | null;
  error: string | null;
}

// ── Store ──────────────────────────────────────────────────────────────

/** The questionnaire as a plan: consecutive questions of one category share a phase. */
export function planFromQuestionnaire(sessionId: string, questions: LoggedQuestion[]): InterviewPlan {
  const phases: PlanPhase[] = [];
  let minute = 0;
  for (const logged of questions) {
    const q = { ...logged.question, id: logged.id };
    const last = phases[phases.length - 1];
    if (last && last.phase === logged.category) {
      last.questions.push(q);
      last.duration_minutes += q.time_minutes;
    } else {
      phases.push({ phase: logged.category, start_minute: minute, duration_minutes: q.time_minutes, questions: [q] });
    }
    minute += q.time_minutes;
  }
  return {
    id: '',
    session_id: sessionId,
    version: questions.length,
    is_active: true,
    phases,
    total_minutes: minute,
    created_at: '',
  };
}

interface PlanStoreState {
  plan: InterviewPlan | null;
  /** Questions used so far in a live-generated interview, in order. */
  questionnaire: LoggedQuestion[];
  planStatus: PlanStatus;
  planError: string | null;
  resume: ResumeState;

  setPlan: (plan: InterviewPlan) => void;
  setPlanStatus: (status: PlanStatus) => void;
  setPlanError: (error: string | null) => void;
  updatePlanPhases: (phases: PlanPhase[]) => void;
  setQuestionnaire: (sessionId: string, questions: LoggedQuestion[]) => void;
  /** Log a used question; returns its index among all questions. */
  addQuestion: (sessionId: string, question: LoggedQuestion) => number;

  setResumeUploading: (filename: string) => void;
  setResumeProcessing: (referenceId: string) => void;
  setResumeCompleted: (data: Record<string, unknown>) => void;
  setResumeFailed: (error: string) => void;
  clearResume: () => void;

  reset: () => void;
}

const INITIAL_RESUME: ResumeState = {
  referenceId: null,
  filename: null,
  status: 'idle',
  structuredData: null,
  error: null,
};

export const usePlanStore = create<PlanStoreState>((set, get) => ({
  plan: null,
  questionnaire: [],
  planStatus: 'idle',
  planError: null,
  resume: { ...INITIAL_RESUME },

  setPlan: (plan) => set({ plan, planStatus: 'ready', planError: null }),
  setPlanStatus: (planStatus) => set({ planStatus }),
  setPlanError: (planError) => set({ planError, planStatus: 'error' }),
  updatePlanPhases: (phases) =>
    set((s) => (s.plan ? { plan: { ...s.plan, phases } } : {})),
  setQuestionnaire: (sessionId, questions) =>
    set({ questionnaire: questions, plan: planFromQuestionnaire(sessionId, questions), planStatus: 'ready' }),
  addQuestion: (sessionId, question) => {
    const questions = [...get().questionnaire, question];
    get().setQuestionnaire(sessionId, questions);
    return questions.length - 1;
  },

  setResumeUploading: (filename) =>
    set({ resume: { ...INITIAL_RESUME, filename, status: 'uploading' } }),
  setResumeProcessing: (referenceId) =>
    set((s) => ({ resume: { ...s.resume, referenceId, status: 'processing' } })),
  setResumeCompleted: (data) =>
    set((s) => ({ resume: { ...s.resume, status: 'completed', structuredData: data } })),
  setResumeFailed: (error) =>
    set((s) => ({ resume: { ...s.resume, status: 'failed', error } })),
  clearResume: () => set({ resume: { ...INITIAL_RESUME } }),

  reset: () =>
    set({ plan: null, questionnaire: [], planStatus: 'idle', planError: null, resume: { ...INITIAL_RESUME } }),
}));
