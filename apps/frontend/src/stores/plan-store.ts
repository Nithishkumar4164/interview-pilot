/**
 * Zustand store for interview plan and resume state.
 */
import { create } from 'zustand';

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
}

export interface PlannedQuestion {
  id: string;
  text: string;
  category: string;
  difficulty: number;
  time_minutes: number;
  alternates: string[];
  hints: QuestionHint[];
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

interface PlanStoreState {
  plan: InterviewPlan | null;
  planStatus: PlanStatus;
  planError: string | null;
  resume: ResumeState;

  setPlan: (plan: InterviewPlan) => void;
  setPlanStatus: (status: PlanStatus) => void;
  setPlanError: (error: string | null) => void;
  updatePlanPhases: (phases: PlanPhase[]) => void;

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

export const usePlanStore = create<PlanStoreState>((set) => ({
  plan: null,
  planStatus: 'idle',
  planError: null,
  resume: { ...INITIAL_RESUME },

  setPlan: (plan) => set({ plan, planStatus: 'ready', planError: null }),
  setPlanStatus: (planStatus) => set({ planStatus }),
  setPlanError: (planError) => set({ planError, planStatus: 'error' }),
  updatePlanPhases: (phases) =>
    set((s) => (s.plan ? { plan: { ...s.plan, phases } } : {})),

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
    set({ plan: null, planStatus: 'idle', planError: null, resume: { ...INITIAL_RESUME } }),
}));
