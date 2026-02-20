export type Recommendation =
  | 'strong_hire'
  | 'hire'
  | 'lean_hire'
  | 'lean_no_hire'
  | 'no_hire';

export type AssessmentCriterion =
  | 'problem_solving'
  | 'code_quality'
  | 'communication'
  | 'hint_responsiveness'
  | 'system_design'
  | 'resume_verification'
  | 'behavioral';

export interface CriterionScore {
  criterion: AssessmentCriterion;
  score: number; // 1-5
  evidence: string[];
  trend: 'improving' | 'stable' | 'declining';
}

export interface KeyMoment {
  timestamp: string;
  title: string;
  description: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  impactOnAssessment: string;
}

export interface Assessment {
  recommendation: Recommendation;
  confidenceLevel: number; // 0-1
  scores: CriterionScore[];
  keyMoments: KeyMoment[];
  strengths: string[];
  areasForGrowth: string[];
  levelComparison: string;
}

export interface ReportData {
  id: string;
  sessionId: string;
  assessment: Assessment;
  hintSummary: {
    totalHintsGiven: number;
    hintsPerQuestion: Record<string, number>;
    overallResponsiveness: number;
  };
  annotatedTranscript: AnnotatedUtterance[];
  providerFailoverLog: FailoverLogEntry[];
  generatedAt: string;
  isFinal: boolean;
}

export interface AnnotatedUtterance {
  transcriptId: string;
  text: string;
  speakerRole: string;
  timestamp: string;
  annotations: Annotation[];
}

export interface Annotation {
  type: 'insight' | 'red_flag' | 'hint' | 'code_event' | 'contradiction';
  text: string;
  severity?: string;
}

export interface FailoverLogEntry {
  timestamp: string;
  taskType: string;
  fromProvider: string;
  toProvider: string;
  reason: string;
}
