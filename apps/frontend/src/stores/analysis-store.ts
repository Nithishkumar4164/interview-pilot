/**
 * Real-time analysis store.
 *
 * Stores analysis events, score updates, red flags, code analysis,
 * and 6-dimension insight evaluations.
 */
import { create } from 'zustand';

// ── Dimension Types ──────────────────────────────────────────────────

export type DimensionScore = 'excellent' | 'good' | 'adequate' | 'weak' | 'poor';

export interface RelevanceDimension {
  score: DimensionScore;
  onTopic: boolean;
  justification: string;
}

export interface BehavioralSignalDimension {
  score: DimensionScore;
  signalsDetected: string[];
  flags: string[];
  justification: string;
}

export interface ContextualConsistencyDimension {
  score: DimensionScore;
  consistent: boolean;
  contradictions: string[];
  justification: string;
}

export interface HintReceptionDimension {
  score: DimensionScore;
  actedOnHint: boolean;
  incorporationLevel: 'full' | 'partial' | 'none';
  justification: string;
}

export interface DepthCompletenessDimension {
  score: DimensionScore;
  keyPointsCovered: string[];
  keyPointsMissed: string[];
  justification: string;
}

export interface CommunicationQualityDimension {
  score: DimensionScore;
  clarity: 'high' | 'medium' | 'low';
  structure: 'high' | 'medium' | 'low';
  confidence: 'high' | 'medium' | 'low';
  justification: string;
}

export interface InsightDimensions {
  relevance: RelevanceDimension;
  behavioralSignal: BehavioralSignalDimension;
  contextualConsistency: ContextualConsistencyDimension;
  hintReception: HintReceptionDimension | null;
  depthCompleteness: DepthCompletenessDimension;
  communicationQuality: CommunicationQualityDimension;
}

// ── Analysis Entry ───────────────────────────────────────────────────

export interface AnalysisEntry {
  answerQuality: number;
  onTrack: boolean;
  insight: string;
  redFlags: string[];
  suggestionForInterviewer: string;
  hintSignal: string;
  timestamp: string;
  provider: string;
  dimensions: InsightDimensions | null;
}

export interface CodeRatingResult {
  questionId: string;
  approachMatch: string;
  approachName: string;
  correctness: string;
  correctnessIssues: string[];
  complexityActual: { time?: string; space?: string };
  complexityExpected: { time?: string; space?: string };
  complexityMatch: boolean;
  pitfallsHit: string[];
  pitfallsAvoided: string[];
  strengths: string[];
  improvementAreas: string[];
  overallScore: number;
  summary: string;
  latencyMs: number;
  provider: string;
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
  scores: ScoreState;
  codeRatingInput: string;
  codeRating: CodeRatingResult | null;
  codeRatingLoading: boolean;

  // Actions
  addAnalysis: (entry: AnalysisEntry) => void;
  restoreAnalyses: (entries: AnalysisEntry[]) => void;
  addRedFlag: (flag: { text: string; timestamp: string; type?: string }) => void;
  updateScores: (scores: Partial<ScoreState>) => void;
  setCodeRatingInput: (input: string) => void;
  setCodeRating: (rating: CodeRatingResult | null) => void;
  setCodeRatingLoading: (loading: boolean) => void;
  clearCodeRating: () => void;
  clear: () => void;
}

const DEFAULT_SCORES: ScoreState = {
  problemSolving: 0,
  codeQuality: 0,
  communication: 0,
  hintResponsiveness: 0,
};

/** Map a DimensionScore string to a 0-5 numeric value. */
function dimensionScoreToNumber(score: DimensionScore): number {
  const map: Record<DimensionScore, number> = {
    excellent: 5,
    good: 4,
    adequate: 3,
    weak: 2,
    poor: 1,
  };
  return map[score] ?? 3;
}

/** Map snake_case dimension payload to camelCase InsightDimensions. */
export function mapDimensions(raw: Record<string, unknown> | null | undefined): InsightDimensions | null {
  if (!raw) return null;

  try {
    const rel = raw.relevance as Record<string, unknown> | undefined;
    const beh = raw.behavioral_signal as Record<string, unknown> | undefined;
    const con = raw.contextual_consistency as Record<string, unknown> | undefined;
    const hint = raw.hint_reception as Record<string, unknown> | null | undefined;
    const dep = raw.depth_completeness as Record<string, unknown> | undefined;
    const comm = raw.communication_quality as Record<string, unknown> | undefined;

    if (!rel || !beh || !con || !dep || !comm) return null;

    return {
      relevance: {
        score: (rel.score as DimensionScore) || 'adequate',
        onTopic: (rel.on_topic as boolean) ?? true,
        justification: (rel.justification as string) || '',
      },
      behavioralSignal: {
        score: (beh.score as DimensionScore) || 'adequate',
        signalsDetected: (beh.signals_detected as string[]) || [],
        flags: (beh.flags as string[]) || [],
        justification: (beh.justification as string) || '',
      },
      contextualConsistency: {
        score: (con.score as DimensionScore) || 'adequate',
        consistent: (con.consistent as boolean) ?? true,
        contradictions: (con.contradictions as string[]) || [],
        justification: (con.justification as string) || '',
      },
      hintReception: hint
        ? {
            score: (hint.score as DimensionScore) || 'adequate',
            actedOnHint: (hint.acted_on_hint as boolean) ?? false,
            incorporationLevel: (hint.incorporation_level as 'full' | 'partial' | 'none') || 'none',
            justification: (hint.justification as string) || '',
          }
        : null,
      depthCompleteness: {
        score: (dep.score as DimensionScore) || 'adequate',
        keyPointsCovered: (dep.key_points_covered as string[]) || [],
        keyPointsMissed: (dep.key_points_missed as string[]) || [],
        justification: (dep.justification as string) || '',
      },
      communicationQuality: {
        score: (comm.score as DimensionScore) || 'adequate',
        clarity: (comm.clarity as 'high' | 'medium' | 'low') || 'medium',
        structure: (comm.structure as 'high' | 'medium' | 'low') || 'medium',
        confidence: (comm.confidence as 'high' | 'medium' | 'low') || 'medium',
        justification: (comm.justification as string) || '',
      },
    };
  } catch {
    return null;
  }
}

export const useAnalysisStore = create<AnalysisStoreState>((set) => ({
  analyses: [],
  latestAnalysis: null,
  redFlags: [],
  scores: { ...DEFAULT_SCORES },
  codeRatingInput: '',
  codeRating: null,
  codeRatingLoading: false,

  addAnalysis: (entry) =>
    set((s) => {
      const analyses = [...s.analyses, entry].slice(-50);
      // Update running score average
      const scored = analyses.filter((a) => a.answerQuality > 0 && a.onTrack);
      const avg = scored.length > 0
        ? scored.reduce((sum, a) => sum + a.answerQuality, 0) / scored.length
        : 0;

      // Derive all dimension-based scores from full history
      const scoreUpdates: Partial<ScoreState> = {
        problemSolving: Math.round(avg * 10) / 10,
      };

      // Running average of communication scores
      const withDims = analyses.filter((a) => a.dimensions && a.onTrack);
      if (withDims.length > 0) {
        const commAvg = withDims.reduce(
          (sum, a) => sum + (a.dimensions ? dimensionScoreToNumber(a.dimensions.communicationQuality.score) : 0),
          0,
        ) / withDims.length;
        scoreUpdates.communication = Math.round(commAvg * 10) / 10;
      }

      // Hint responsiveness from hint_reception dimension
      const hintEntries = analyses.filter((a) => a.dimensions?.hintReception && a.onTrack);
      if (hintEntries.length > 0) {
        const hintAvg = hintEntries.reduce(
          (sum, a) => sum + (a.dimensions?.hintReception ? dimensionScoreToNumber(a.dimensions.hintReception.score) : 0),
          0,
        ) / hintEntries.length;
        scoreUpdates.hintResponsiveness = Math.round(hintAvg * 10) / 10;
      }

      return {
        analyses,
        latestAnalysis: entry,
        scores: {
          ...s.scores,
          ...scoreUpdates,
        },
      };
    }),

  restoreAnalyses: (entries) =>
    set(() => {
      const analyses = entries.slice(-50);
      const scored = analyses.filter((a) => a.answerQuality > 0 && a.onTrack);
      const avg = scored.length > 0
        ? scored.reduce((sum, a) => sum + a.answerQuality, 0) / scored.length
        : 0;

      const scoreUpdates: Partial<ScoreState> = {
        problemSolving: Math.round(avg * 10) / 10,
      };

      const withDims = analyses.filter((a) => a.dimensions && a.onTrack);
      if (withDims.length > 0) {
        const commAvg = withDims.reduce(
          (sum, a) => sum + (a.dimensions ? dimensionScoreToNumber(a.dimensions.communicationQuality.score) : 0),
          0,
        ) / withDims.length;
        scoreUpdates.communication = Math.round(commAvg * 10) / 10;
      }

      const hintEntries = analyses.filter((a) => a.dimensions?.hintReception && a.onTrack);
      if (hintEntries.length > 0) {
        const hintAvg = hintEntries.reduce(
          (sum, a) => sum + (a.dimensions?.hintReception ? dimensionScoreToNumber(a.dimensions.hintReception.score) : 0),
          0,
        ) / hintEntries.length;
        scoreUpdates.hintResponsiveness = Math.round(hintAvg * 10) / 10;
      }

      return {
        analyses,
        latestAnalysis: analyses.length > 0 ? analyses[analyses.length - 1] : null,
        scores: { ...DEFAULT_SCORES, ...scoreUpdates },
      };
    }),

  addRedFlag: (flag) =>
    set((s) => ({ redFlags: [...s.redFlags, flag].slice(-20) })),

  updateScores: (scores) =>
    set((s) => ({ scores: { ...s.scores, ...scores } })),

  setCodeRatingInput: (input) => set({ codeRatingInput: input }),

  setCodeRating: (rating) => set((s) => ({
    codeRating: rating,
    codeRatingLoading: false,
    scores: {
      ...s.scores,
      ...(rating ? { codeQuality: rating.overallScore } : {}),
    },
  })),

  setCodeRatingLoading: (loading) => set({ codeRatingLoading: loading }),

  clearCodeRating: () => set({ codeRatingInput: '', codeRating: null, codeRatingLoading: false }),

  clear: () =>
    set({
      analyses: [],
      latestAnalysis: null,
      redFlags: [],
      scores: { ...DEFAULT_SCORES },
      codeRatingInput: '',
      codeRating: null,
      codeRatingLoading: false,
    }),
}));
