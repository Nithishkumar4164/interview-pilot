/**
 * Transcript event store.
 *
 * Stores live transcript events with speaker identification,
 * handles both real-time and retroactive (catch-up) events.
 */
import { create } from 'zustand';

export type SpeakerRole = 'interviewer' | 'candidate' | 'unknown';

export interface TranscriptEntry {
  id: string;
  speakerRole: SpeakerRole;
  text: string;
  isFinal: boolean;
  confidence: number;
  timestamp: string;
  channel: string;
  isRetroactive: boolean;
}

interface TranscriptState {
  entries: TranscriptEntry[];
  interimEntry: TranscriptEntry | null;

  // Actions
  addEntry: (entry: TranscriptEntry) => void;
  setInterim: (entry: TranscriptEntry | null) => void;
  addCatchupEntries: (entries: TranscriptEntry[]) => void;
  clear: () => void;
}

const MAX_ENTRIES = 500;

export const useTranscriptStore = create<TranscriptState>((set) => ({
  entries: [],
  interimEntry: null,

  addEntry: (entry) =>
    set((s) => {
      if (entry.isFinal) {
        const entries = [...s.entries, entry];
        // Keep max entries to prevent memory issues
        if (entries.length > MAX_ENTRIES) {
          entries.splice(0, entries.length - MAX_ENTRIES);
        }
        return { entries, interimEntry: null };
      }
      // Interim result — update the transient entry
      return { interimEntry: entry };
    }),

  setInterim: (entry) => set({ interimEntry: entry }),

  addCatchupEntries: (entries) =>
    set((s) => ({
      entries: [...entries, ...s.entries].slice(0, MAX_ENTRIES),
    })),

  clear: () => set({ entries: [], interimEntry: null }),
}));
