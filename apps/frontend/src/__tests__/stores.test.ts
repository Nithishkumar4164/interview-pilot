/**
 * Zustand store unit tests.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useSessionStore } from '@/stores/session-store';
import { useTranscriptStore } from '@/stores/transcript-store';
import { useAnalysisStore } from '@/stores/analysis-store';
import { useHintStore } from '@/stores/hint-store';
import { useOfflineStore } from '@/stores/offline-store';

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
    });

    const state = useTranscriptStore.getState();
    expect(state.entries).toHaveLength(1);
    expect(state.entries[0].text).toBe('Hello');
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
      });
    }
    expect(useTranscriptStore.getState().entries.length).toBeLessThanOrEqual(500);
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

  it('addRedFlag adds to red flags list', () => {
    useAnalysisStore.getState().addRedFlag({
      text: 'Contradiction detected',
      timestamp: '2024-01-01T00:00:00Z',
      type: 'contradiction',
    });
    expect(useAnalysisStore.getState().redFlags).toHaveLength(1);
    expect(useAnalysisStore.getState().redFlags[0].type).toBe('contradiction');
  });

  it('setCodeAnalysis updates code analysis state', () => {
    useAnalysisStore.getState().setCodeAnalysis({
      codeLength: 500,
      language: 'python',
      source: 'coderpad',
      progressState: 'partial',
      detectedApproach: { name: 'hash_map', confidence: 0.85 },
      isStuck: false,
      bugs: [],
      timestamp: '2024-01-01T00:00:00Z',
    });

    const ca = useAnalysisStore.getState().codeAnalysis;
    expect(ca?.language).toBe('python');
    expect(ca?.progressState).toBe('partial');
    expect(ca?.detectedApproach?.name).toBe('hash_map');
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
