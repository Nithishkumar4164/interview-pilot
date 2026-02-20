import type { HintEvent } from './hints';

export type DegradationLevel = 'healthy' | 'llm_unavailable' | 'backend_unreachable';

export interface OfflineStateSnapshot {
  sessionId: string;
  schemaVersion: number;
  transcriptCount: number;
  lastTranscriptTimestamp: string | null;
  analysisCount: number;
  codeSnapshotCount: number;
  hintHistory: HintEvent[];
  activeQuestionId: string | null;
  scores: Record<string, number>;
  coverageTracker: Record<string, boolean>;
  planProgress: { currentPhaseIndex: number; currentQuestionIndex: number };
  degradationLevel: DegradationLevel;
  capturedAt: string;
}

export interface CatchUpSummary {
  sessionId: string;
  missedDurationMs: number;
  eventsProcessed: number;
  keyFindings: string[];
  factsExtracted: number;
  contradictionsFound: number;
  summaryText: string;
  processedAt: string;
}
