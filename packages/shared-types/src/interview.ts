export type InterviewLevel =
  | 'intern'
  | 'sde1'
  | 'sde2'
  | 'senior'
  | 'staff'
  | 'principal';

export type InterviewPhase =
  | 'introduction'
  | 'resume_verification'
  | 'coding'
  | 'system_design'
  | 'behavioral'
  | 'wrapup';

export type SessionStatus =
  | 'planning'
  | 'ready'
  | 'live'
  | 'paused'
  | 'ended';

export interface InterviewSession {
  id: string;
  userId: string;
  status: SessionStatus;
  level: InterviewLevel;
  durationMinutes: number;
  focusAreas: string[];
  jobDescription?: string;
  customNotes?: string;
  planId?: string;
  startedAt?: string;
  endedAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface PlannedQuestion {
  id: string;
  text: string;
  category: InterviewPhase;
  difficulty: number;
  timeMinutes: number;
  alternates: string[];
  hints: QuestionHint[];
  optimalApproaches?: ApproachPattern[];
  commonPitfalls?: string[];
}

export interface QuestionHint {
  level: 1 | 2 | 3;
  text: string;
}

export interface ApproachPattern {
  name: string;
  dataStructures: string[];
  expectedComplexity: { time: string; space: string };
  keySteps: string[];
}

export interface InterviewPlan {
  id: string;
  sessionId: string;
  version: number;
  isActive: boolean;
  phases: PlanPhase[];
  totalMinutes: number;
  createdAt: string;
}

export interface PlanPhase {
  phase: InterviewPhase;
  startMinute: number;
  durationMinutes: number;
  questions: PlannedQuestion[];
}
