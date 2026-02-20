export type HintLevel = 1 | 2 | 3;

export type HintResponseQuality = 'none' | 'partial' | 'full';

export interface HintEvent {
  id: string;
  sessionId: string;
  questionId: string;
  level: HintLevel;
  text: string;
  displayedAt: string;
  interviewerUsed: boolean;
  candidateResponseQuality: HintResponseQuality;
  timeToRespondMs: number | null;
}

export interface HintResponseMetrics {
  totalHintsGiven: number;
  averageResponseTimeMs: number | null;
  incorporationRate: number; // 0-1, ratio of partial+full to total
  levelDistribution: Record<HintLevel, number>;
  independenceTrajectory: 'improving' | 'stable' | 'declining';
}

export interface HintState {
  questionId: string;
  currentLevel: HintLevel;
  availableHints: Array<{ level: HintLevel; text: string }>;
  activeHint: HintEvent | null;
  history: HintEvent[];
  autoAdvanceAt: string | null; // ISO timestamp when auto-advance triggers
}
