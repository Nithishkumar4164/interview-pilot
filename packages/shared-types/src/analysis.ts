export interface AnalysisEvent {
  id: string;
  sessionId: string;
  transcriptId: string;
  answerQuality: number; // 1-5
  onTrack: boolean;
  insight: string;
  redFlags: RedFlag[];
  suggestionForInterviewer: string;
  hintIncorporationSignal?: string;
  timestamp: string;
}

export interface RedFlag {
  type: 'contradiction' | 'evasion' | 'inconsistency' | 'concern';
  severity: 'low' | 'medium' | 'high';
  description: string;
  suggestedProbe?: string;
}

export type CodeProgressState =
  | 'empty'
  | 'skeleton'
  | 'partial'
  | 'near_complete'
  | 'complete';

export interface CodeAnalysis {
  id: string;
  sessionId: string;
  code: string;
  language: string;
  detectedApproach?: string;
  progressState: CodeProgressState;
  isStuck: boolean;
  timeComplexity?: string;
  spaceComplexity?: string;
  bugs: CodeBug[];
  timestamp: string;
}

export interface CodeBug {
  severity: 'minor' | 'major' | 'critical';
  description: string;
  location?: string;
}

export interface ContradictionEvent {
  id: string;
  sessionId: string;
  topic: string;
  earlierClaim: string;
  currentClaim: string;
  severity: 'low' | 'medium' | 'high';
  suggestedProbe: string;
  timestamp: string;
}
