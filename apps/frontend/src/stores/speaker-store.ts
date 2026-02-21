/**
 * Speaker management store.
 *
 * Tracks Root Speakers (user-created labels) and Speech Vectors
 * (Deepgram speaker IDs) with parent-child relationships.
 *
 * Seeded with the two default root speakers so the UI is never empty.
 */
import { create } from 'zustand';

export interface RootSpeaker {
  id: string;
  label: string;
  color: string;
  is_default: boolean;
  speech_vector_ids: string[];
}

export interface SpeechVector {
  id: string;
  root_speaker_id: string;
  deepgram_speaker_id: number | null;
  channel: string;
  utterance_count: number;
  transcript_ids: string[];
  is_virtual: boolean;
}

export interface SpeakerSnapshot {
  root_speakers: Record<string, RootSpeaker>;
  speech_vectors: Record<string, SpeechVector>;
}

export interface TranscriptAssignment {
  transcript_id: string;
  new_root_speaker_id: string;
  new_speech_vector_id: string;
}

// Must match SpeakerService.DEFAULT_*_RS on the backend
const DEFAULT_ROOT_SPEAKERS: Record<string, RootSpeaker> = {
  'rs-default-interviewer': {
    id: 'rs-default-interviewer',
    label: 'Interviewer',
    color: '#818cf8',
    is_default: true,
    speech_vector_ids: [],
  },
  'rs-default-candidate': {
    id: 'rs-default-candidate',
    label: 'Candidate',
    color: '#34d399',
    is_default: true,
    speech_vector_ids: [],
  },
};

export interface SvTranscriptPreview {
  id: string;
  text: string;
}

interface SpeakerState {
  rootSpeakers: Record<string, RootSpeaker>;
  speechVectors: Record<string, SpeechVector>;
  /** Cached transcript previews per speech vector, keyed by sv_id */
  svTranscripts: Record<string, SvTranscriptPreview[]>;

  // Actions
  applySnapshot: (snapshot: SpeakerSnapshot) => void;
  applyTranscriptAssignment: (assignment: TranscriptAssignment) => void;
  setSvTranscripts: (svId: string, transcripts: SvTranscriptPreview[]) => void;
  clear: () => void;
}

export const useSpeakerStore = create<SpeakerState>((set) => ({
  rootSpeakers: { ...DEFAULT_ROOT_SPEAKERS },
  speechVectors: {},
  svTranscripts: {},

  applySnapshot: (snapshot) =>
    set({
      rootSpeakers: snapshot.root_speakers,
      speechVectors: snapshot.speech_vectors,
    }),

  applyTranscriptAssignment: (_assignment) =>
    set((s) => {
      // The full snapshot broadcast that follows will update the store,
      // but we can optimistically note the assignment here if needed.
      return s;
    }),

  setSvTranscripts: (svId, transcripts) =>
    set((s) => ({
      svTranscripts: { ...s.svTranscripts, [svId]: transcripts },
    })),

  clear: () => set({ rootSpeakers: { ...DEFAULT_ROOT_SPEAKERS }, speechVectors: {}, svTranscripts: {} }),
}));
