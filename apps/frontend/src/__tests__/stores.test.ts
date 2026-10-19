/**
 * Zustand store unit tests.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useSessionStore } from '@/stores/session-store';
import { useTranscriptStore } from '@/stores/transcript-store';
import { useAnalysisStore } from '@/stores/analysis-store';
import { useHintStore } from '@/stores/hint-store';
import { useOfflineStore } from '@/stores/offline-store';
import { useSpeakerStore } from '@/stores/speaker-store';

// ── Session Store ─────────────────────────────────────────────────────

describe('useSessionStore', () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
  });

  it('initializes with idle status', () => {
    const state = useSessionStore.getState();
    expect(state.status).toBe('idle');
    expect(state.sessionId).toBeNull();
    expect(state.elapsedMs).toBe(0);
  });

  it('setSession updates id, level, duration and sets status to ready', () => {
    useSessionStore.getState().setSession('sess-1', 'SDE-2', 60);
    const state = useSessionStore.getState();
    expect(state.sessionId).toBe('sess-1');
    expect(state.level).toBe('SDE-2');
    expect(state.durationMinutes).toBe(60);
    expect(state.status).toBe('ready');
  });

  it('setStarted sets timestamp and status to live', () => {
    const now = new Date().toISOString();
    useSessionStore.getState().setStarted(now);
    const state = useSessionStore.getState();
    expect(state.startedAt).toBe(now);
    expect(state.status).toBe('live');
  });

  it('tickElapsed accumulates time', () => {
    useSessionStore.getState().tickElapsed(1000);
    useSessionStore.getState().tickElapsed(500);
    expect(useSessionStore.getState().elapsedMs).toBe(1500);
  });

  it('setCurrentQuestion updates question tracking', () => {
    useSessionStore.getState().setCurrentQuestion(2, 5, 'Implement a LRU cache');
    const state = useSessionStore.getState();
    expect(state.currentQuestionIndex).toBe(2);
    expect(state.totalQuestions).toBe(5);
    expect(state.currentQuestionText).toBe('Implement a LRU cache');
  });

  it('reset restores defaults', () => {
    useSessionStore.getState().setSession('sess-1', 'SDE-3', 90);
    useSessionStore.getState().tickElapsed(5000);
    useSessionStore.getState().reset();
    const state = useSessionStore.getState();
    expect(state.sessionId).toBeNull();
    expect(state.status).toBe('idle');
    expect(state.elapsedMs).toBe(0);
  });
});

// ── Transcript Store ──────────────────────────────────────────────────

describe('useTranscriptStore', () => {
  beforeEach(() => {
    useTranscriptStore.getState().clear();
  });

  it('starts empty', () => {
    const state = useTranscriptStore.getState();
    expect(state.entries).toHaveLength(0);
    expect(state.interimEntry).toBeNull();
  });

  it('addEntry with isFinal=true appends to entries', () => {
    useTranscriptStore.getState().addEntry({
      id: '1',
      speakerRole: 'candidate',
      text: 'Hello',
      isFinal: true,
      confidence: 0.95,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'tab',
      isRetroactive: false,
      rootSpeakerId: 'rs-default-candidate',
      speechVectorId: 'sv-dg-tab-0',
    });

    const state = useTranscriptStore.getState();
    expect(state.entries).toHaveLength(1);
    expect(state.entries[0].text).toBe('Hello');
    expect(state.entries[0].rootSpeakerId).toBe('rs-default-candidate');
    expect(state.entries[0].speechVectorId).toBe('sv-dg-tab-0');
    expect(state.interimEntry).toBeNull();
  });

  it('addEntry with isFinal=false sets interim entry', () => {
    useTranscriptStore.getState().addEntry({
      id: '1',
      speakerRole: 'candidate',
      text: 'Hel...',
      isFinal: false,
      confidence: 0.5,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'tab',
      isRetroactive: false,
      rootSpeakerId: '',
      speechVectorId: '',
    });

    const state = useTranscriptStore.getState();
    expect(state.entries).toHaveLength(0);
    expect(state.interimEntry).not.toBeNull();
    expect(state.interimEntry!.text).toBe('Hel...');
  });

  it('addCatchupEntries prepends retroactive entries', () => {
    useTranscriptStore.getState().addEntry({
      id: '2',
      speakerRole: 'candidate',
      text: 'Real-time message',
      isFinal: true,
      confidence: 1,
      timestamp: '2024-01-01T00:01:00Z',
      channel: 'tab',
      isRetroactive: false,
      rootSpeakerId: '',
      speechVectorId: '',
    });

    useTranscriptStore.getState().addCatchupEntries([
      {
        id: '1',
        speakerRole: 'candidate',
        text: 'Catch-up message',
        isFinal: true,
        confidence: 0.9,
        timestamp: '2024-01-01T00:00:00Z',
        channel: 'tab',
        isRetroactive: true,
        rootSpeakerId: '',
        speechVectorId: '',
      },
    ]);

    const state = useTranscriptStore.getState();
    expect(state.entries).toHaveLength(2);
    expect(state.entries[0].text).toBe('Catch-up message');
    expect(state.entries[1].text).toBe('Real-time message');
  });

  it('limits entries to MAX_ENTRIES (500)', () => {
    for (let i = 0; i < 510; i++) {
      useTranscriptStore.getState().addEntry({
        id: String(i),
        speakerRole: 'candidate',
        text: `Message ${i}`,
        isFinal: true,
        confidence: 1,
        timestamp: '2024-01-01T00:00:00Z',
        channel: 'tab',
        isRetroactive: false,
        rootSpeakerId: '',
        speechVectorId: '',
      });
    }
    expect(useTranscriptStore.getState().entries.length).toBeLessThanOrEqual(500);
  });

  it('updateEntrySpeaker updates rootSpeakerId and speechVectorId', () => {
    useTranscriptStore.getState().addEntry({
      id: 't1',
      speakerRole: 'candidate',
      text: 'Some text',
      isFinal: true,
      confidence: 1,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'tab',
      isRetroactive: false,
      rootSpeakerId: 'rs-default-candidate',
      speechVectorId: 'sv-dg-tab-0',
    });

    useTranscriptStore.getState().updateEntrySpeaker('t1', 'rs-custom-1', 'sv-virt-abc');
    const entry = useTranscriptStore.getState().entries[0];
    expect(entry.rootSpeakerId).toBe('rs-custom-1');
    expect(entry.speechVectorId).toBe('sv-virt-abc');
  });
});

// ── Analysis Store ────────────────────────────────────────────────────

describe('useAnalysisStore', () => {
  beforeEach(() => {
    useAnalysisStore.getState().clear();
  });

  it('starts with zero scores', () => {
    const state = useAnalysisStore.getState();
    expect(state.scores.problemSolving).toBe(0);
    expect(state.scores.communication).toBe(0);
    expect(state.latestAnalysis).toBeNull();
  });

  it('addAnalysis updates latestAnalysis and computes running average', () => {
    const entry1 = {
      answerQuality: 4,
      onTrack: true,
      insight: 'Good approach',
      redFlags: [],
      suggestionForInterviewer: '',
      hintSignal: 'none',
      timestamp: '2024-01-01T00:00:00Z',
      provider: 'mock',
      dimensions: null,
    };
    const entry2 = {
      ...entry1,
      answerQuality: 2,
      insight: 'Needs improvement',
    };

    useAnalysisStore.getState().addAnalysis(entry1);
    expect(useAnalysisStore.getState().scores.problemSolving).toBe(4);
    expect(useAnalysisStore.getState().latestAnalysis?.insight).toBe('Good approach');

    useAnalysisStore.getState().addAnalysis(entry2);
    // Average of 4 and 2 = 3.0
    expect(useAnalysisStore.getState().scores.problemSolving).toBe(3);
    expect(useAnalysisStore.getState().latestAnalysis?.insight).toBe('Needs improvement');
  });

  it('addRedFlags appends flags', () => {
    useAnalysisStore.getState().addRedFlags([
      { id: 'f1', text: 'Avoided the question', timestamp: '2024-01-01T00:00:00Z' },
    ]);
    expect(useAnalysisStore.getState().redFlags).toHaveLength(1);
    expect(useAnalysisStore.getState().redFlags[0].text).toBe('Avoided the question');
  });

  it('updateScores merges partial scores', () => {
    useAnalysisStore.getState().updateScores({ communication: 3.5, codeQuality: 4 });
    const scores = useAnalysisStore.getState().scores;
    expect(scores.communication).toBe(3.5);
    expect(scores.codeQuality).toBe(4);
    expect(scores.hintResponsiveness).toBe(0); // Unchanged
  });
});

// ── Hint Store ────────────────────────────────────────────────────────

describe('useHintStore', () => {
  beforeEach(() => {
    useHintStore.getState().clear();
  });

  it('starts at level 0 with no active hint', () => {
    const state = useHintStore.getState();
    expect(state.currentLevel).toBe(0);
    expect(state.activeHint).toBeNull();
    expect(state.history).toHaveLength(0);
  });

  it('setActiveHint updates level, activeHint, and appends to history', () => {
    const hint = {
      id: 'h1',
      questionId: 'q1',
      level: 1 as const,
      text: 'Think about data structures',
      displayedAt: '2024-01-01T00:00:00Z',
      interviewerUsed: false,
      candidateResponseQuality: 'none' as const,
      timeToRespondMs: null,
    };

    useHintStore.getState().setActiveHint(hint);
    const state = useHintStore.getState();
    expect(state.currentLevel).toBe(1);
    expect(state.activeHint?.text).toBe('Think about data structures');
    expect(state.history).toHaveLength(1);
  });

  it('acknowledgeHint marks quality on history and active hint', () => {
    const hint = {
      id: 'h1',
      questionId: 'q1',
      level: 2 as const,
      text: 'Consider a hash map',
      displayedAt: '2024-01-01T00:00:00Z',
      interviewerUsed: false,
      candidateResponseQuality: 'none' as const,
      timeToRespondMs: null,
    };

    useHintStore.getState().setActiveHint(hint);
    useHintStore.getState().acknowledgeHint('h1', 'full');

    const state = useHintStore.getState();
    expect(state.activeHint?.interviewerUsed).toBe(true);
    expect(state.activeHint?.candidateResponseQuality).toBe('full');
    expect(state.history[0].interviewerUsed).toBe(true);
    expect(state.history[0].candidateResponseQuality).toBe('full');
  });

  it('setHintSignal updates signal', () => {
    useHintStore.getState().setHintSignal('guide');
    expect(useHintStore.getState().hintSignal).toBe('guide');
  });
});

// ── Offline Store ─────────────────────────────────────────────────────

describe('useOfflineStore', () => {
  beforeEach(() => {
    useOfflineStore.getState().reset();
  });

  it('starts disconnected at degradation 0', () => {
    const state = useOfflineStore.getState();
    expect(state.wsStatus).toBe('disconnected');
    expect(state.degradationLevel).toBe(0);
  });

  it('connected WS + connected backend = degradation 0', () => {
    useOfflineStore.getState().setWSStatus('connected');
    useOfflineStore.getState().setBackendStatus('connected');
    expect(useOfflineStore.getState().degradationLevel).toBe(0);
  });

  it('connected WS + disconnected backend = degradation 1', () => {
    useOfflineStore.getState().setWSStatus('connected');
    useOfflineStore.getState().setBackendStatus('disconnected');
    expect(useOfflineStore.getState().degradationLevel).toBe(1);
  });

  it('disconnected WS + connected backend = degradation 2', () => {
    useOfflineStore.getState().setWSStatus('disconnected');
    useOfflineStore.getState().setBackendStatus('connected');
    expect(useOfflineStore.getState().degradationLevel).toBe(2);
  });

  it('both disconnected = degradation 3', () => {
    useOfflineStore.getState().setWSStatus('disconnected');
    useOfflineStore.getState().setBackendStatus('disconnected');
    expect(useOfflineStore.getState().degradationLevel).toBe(3);
  });

  it('setProviderStatus tracks individual providers', () => {
    useOfflineStore.getState().setProviderStatus('anthropic', 'healthy');
    useOfflineStore.getState().setProviderStatus('openai', 'degraded');
    const statuses = useOfflineStore.getState().providerStatuses;
    expect(statuses.anthropic).toBe('healthy');
    expect(statuses.openai).toBe('degraded');
  });

  it('setManualNotes stores notes', () => {
    useOfflineStore.getState().setManualNotes('Candidate seems nervous');
    expect(useOfflineStore.getState().manualNotes).toBe('Candidate seems nervous');
  });
});

// ── Speaker Store ─────────────────────────────────────────────────────

describe('useSpeakerStore', () => {
  beforeEach(() => {
    useSpeakerStore.getState().clear();
  });

  it('starts with default root speakers seeded', () => {
    const state = useSpeakerStore.getState();
    expect(Object.keys(state.rootSpeakers)).toHaveLength(2);
    expect(state.rootSpeakers['rs-default-interviewer'].label).toBe('Interviewer');
    expect(state.rootSpeakers['rs-default-candidate'].label).toBe('Candidate');
    expect(Object.keys(state.speechVectors)).toHaveLength(0);
  });

  it('applySnapshot replaces root speakers and speech vectors', () => {
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
        'rs-default-interviewer': {
          id: 'rs-default-interviewer',
          label: 'Interviewer',
          color: '#818cf8',
          is_default: true,
          speech_vector_ids: ['sv-dg-mic-0'],
        },
        'rs-default-candidate': {
          id: 'rs-default-candidate',
          label: 'Candidate',
          color: '#34d399',
          is_default: true,
          speech_vector_ids: ['sv-dg-tab-0'],
        },
      },
      speech_vectors: {
        'sv-dg-mic-0': {
          id: 'sv-dg-mic-0',
          root_speaker_id: 'rs-default-interviewer',
          deepgram_speaker_id: 0,
          channel: 'mic',
          utterance_count: 5,
          transcript_ids: [],
          is_virtual: false,
        },
        'sv-dg-tab-0': {
          id: 'sv-dg-tab-0',
          root_speaker_id: 'rs-default-candidate',
          deepgram_speaker_id: 0,
          channel: 'tab',
          utterance_count: 12,
          transcript_ids: [],
          is_virtual: false,
        },
      },
    });

    const state = useSpeakerStore.getState();
    expect(Object.keys(state.rootSpeakers)).toHaveLength(2);
    expect(state.rootSpeakers['rs-default-interviewer'].label).toBe('Interviewer');
    expect(state.rootSpeakers['rs-default-candidate'].label).toBe('Candidate');
    expect(Object.keys(state.speechVectors)).toHaveLength(2);
    expect(state.speechVectors['sv-dg-mic-0'].utterance_count).toBe(5);
  });

  it('applySnapshot overwrites previous state', () => {
    // Apply first snapshot
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
        'rs-1': {
          id: 'rs-1',
          label: 'Speaker A',
          color: '#ff0000',
          is_default: false,
          speech_vector_ids: [],
        },
      },
      speech_vectors: {},
    });
    expect(Object.keys(useSpeakerStore.getState().rootSpeakers)).toHaveLength(1);

    // Apply second snapshot — overwrites first
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
        'rs-2': {
          id: 'rs-2',
          label: 'Speaker B',
          color: '#00ff00',
          is_default: false,
          speech_vector_ids: [],
        },
        'rs-3': {
          id: 'rs-3',
          label: 'Speaker C',
          color: '#0000ff',
          is_default: false,
          speech_vector_ids: [],
        },
      },
      speech_vectors: {},
    });

    const state = useSpeakerStore.getState();
    expect(Object.keys(state.rootSpeakers)).toHaveLength(2);
    expect(state.rootSpeakers['rs-1']).toBeUndefined();
    expect(state.rootSpeakers['rs-2'].label).toBe('Speaker B');
  });

  it('clear resets to default speakers', () => {
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
        'rs-1': {
          id: 'rs-1',
          label: 'Test',
          color: '#fff',
          is_default: false,
          speech_vector_ids: [],
        },
      },
      speech_vectors: {
        'sv-1': {
          id: 'sv-1',
          root_speaker_id: 'rs-1',
          deepgram_speaker_id: 0,
          channel: 'tab',
          utterance_count: 3,
          transcript_ids: [],
          is_virtual: false,
        },
      },
    });

    useSpeakerStore.getState().clear();
    const state = useSpeakerStore.getState();
    expect(Object.keys(state.rootSpeakers)).toHaveLength(2);
    expect(state.rootSpeakers['rs-default-interviewer']).toBeDefined();
    expect(state.rootSpeakers['rs-default-candidate']).toBeDefined();
    expect(Object.keys(state.speechVectors)).toHaveLength(0);
  });
});
