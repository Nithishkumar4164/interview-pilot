export type SpeakerRole =
  | 'interviewer'
  | 'candidate'
  | 'second_interviewer'
  | 'recruiter'
  | 'unknown';

export interface TranscriptEvent {
  id: string;
  sessionId: string;
  speakerRole: SpeakerRole;
  text: string;
  isFinal: boolean;
  confidence: number;
  timestamp: string;
  wordTimestamps?: WordTimestamp[];
  isRetroactive: boolean;
  channel: 'tab' | 'microphone';
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  confidence: number;
}

export interface SpeakerCalibration {
  speakerId: string;
  role: SpeakerRole;
  confirmedByInterviewer: boolean;
}
