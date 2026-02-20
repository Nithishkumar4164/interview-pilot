export type WSMessageType =
  | 'transcript'
  | 'analysis'
  | 'code_analysis'
  | 'hint'
  | 'red_flag'
  | 'contradiction'
  | 'phase_change'
  | 'catchup'
  | 'provider_status'
  | 'speaker_calibration'
  | 'score_update'
  | 'error';

export interface WSMessage<T = unknown> {
  type: WSMessageType;
  sessionId: string;
  timestamp: string;
  payload: T;
}

export interface AudioChunkMetadata {
  channel: 'tab' | 'microphone';
  sampleRate: number;
  encoding: 'pcm16';
  chunkDurationMs: number;
  isBuffered: boolean;
  originalTimestamp?: string;
}
