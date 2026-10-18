/**
 * Dashboard component rendering tests.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SessionBar } from '@/components/dashboard/SessionBar';
import { TranscriptPanel } from '@/components/dashboard/TranscriptPanel';
import { HintPanel } from '@/components/dashboard/HintPanel';
import { ScoreRings } from '@/components/dashboard/ScoreRings';
import { SpeakersPanel } from '@/components/dashboard/SpeakersPanel';
import { useSessionStore } from '@/stores/session-store';
import { useTranscriptStore } from '@/stores/transcript-store';
import { useAnalysisStore } from '@/stores/analysis-store';
import { useHintStore } from '@/stores/hint-store';
import { useOfflineStore } from '@/stores/offline-store';
import { useSpeakerStore } from '@/stores/speaker-store';
import { WebSocketProvider } from '@/contexts/WebSocketContext';

// jsdom doesn't have scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// Helper to wrap components that need WebSocket context
function withWS(ui: React.ReactElement, send = vi.fn()) {
  return <WebSocketProvider value={{ send }}>{ui}</WebSocketProvider>;
}

// ── SessionBar ────────────────────────────────────────────────────────

describe('SessionBar', () => {
  const handlers = {
    onPause: vi.fn(),
    onResume: vi.fn(),
    onEnd: vi.fn(),
    onToggleVideo: vi.fn(),
    videoActive: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.getState().reset();
    useOfflineStore.getState().reset();
  });

  it('renders timer and status', () => {
    useSessionStore.getState().setSession('s1', 'SDE-2', 45);
    useSessionStore.getState().setStatus('live');
    useOfflineStore.getState().setWSStatus('connected');
    useOfflineStore.getState().setBackendStatus('connected');

    render(<SessionBar {...handlers} />);
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByText('SDE-2')).toBeInTheDocument();
  });

  it('shows Pause button when live', () => {
    useSessionStore.getState().setStatus('live');
    render(<SessionBar {...handlers} />);
    expect(screen.getByText('Pause')).toBeInTheDocument();
  });

  it('shows Resume button when paused', () => {
    useSessionStore.getState().setStatus('paused');
    render(<SessionBar {...handlers} />);
    expect(screen.getByText('Resume')).toBeInTheDocument();
  });

  it('calls onEnd when End Interview clicked', () => {
    useSessionStore.getState().setStatus('live');
    render(<SessionBar {...handlers} />);
    fireEvent.click(screen.getByText('End Interview'));
    expect(handlers.onEnd).toHaveBeenCalledOnce();
  });

  it('ignores End Interview clicks while ending is in flight', async () => {
    // Ending takes seconds (the backend drains the session first). A second click
    // opened a second confirm, which froze the page and stalled the first end's navigation.
    useSessionStore.getState().setStatus('live');
    let finish!: () => void;
    const onEnd = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    render(<SessionBar {...handlers} onEnd={onEnd} />);

    fireEvent.click(screen.getByText('End Interview'));
    const ending = screen.getByRole('button', { name: 'Ending…' });
    expect(ending).toBeDisabled();
    fireEvent.click(ending);
    expect(onEnd).toHaveBeenCalledOnce();

    finish();
    expect(await screen.findByRole('button', { name: 'End Interview' })).toBeEnabled();
  });

  it('formats time correctly', () => {
    useSessionStore.getState().setSession('s1', 'SDE-2', 45);
    useSessionStore.getState().tickElapsed(125000); // 2:05
    render(<SessionBar {...handlers} />);
    expect(screen.getByText('2:05')).toBeInTheDocument();
  });
});

// ── TranscriptPanel ───────────────────────────────────────────────────

describe('TranscriptPanel', () => {
  beforeEach(() => {
    useTranscriptStore.getState().clear();
    useAnalysisStore.getState().clear();
    useSpeakerStore.getState().clear();
  });

  it('renders transcript heading', () => {
    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('Transcript')).toBeInTheDocument();
  });

  it('renders transcript entries', () => {
    useTranscriptStore.getState().addEntry({
      id: '1',
      speakerRole: 'candidate',
      text: 'I would use a hash map',
      isFinal: true,
      confidence: 0.95,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'tab',
      isRetroactive: false,
      rootSpeakerId: '',
      speechVectorId: '',
    });

    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('I would use a hash map')).toBeInTheDocument();
    expect(screen.getByText('candidate')).toBeInTheDocument();
  });

  it('renders speaker label from root speaker when available', () => {
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
        'rs-default-interviewer': {
          id: 'rs-default-interviewer',
          label: 'Interviewer',
          color: '#818cf8',
          is_default: true,
          speech_vector_ids: [],
        },
      },
      speech_vectors: {},
    });

    useTranscriptStore.getState().addEntry({
      id: '1',
      speakerRole: 'interviewer',
      text: 'Can you explain?',
      isFinal: true,
      confidence: 1,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'mic',
      isRetroactive: false,
      rootSpeakerId: 'rs-default-interviewer',
      speechVectorId: 'sv-dg-mic-0',
    });

    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('Interviewer')).toBeInTheDocument();
  });

  it('falls back to "You" for interviewer without root speaker', () => {
    useTranscriptStore.getState().addEntry({
      id: '1',
      speakerRole: 'interviewer',
      text: 'Can you explain your approach?',
      isFinal: true,
      confidence: 1,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'mic',
      isRetroactive: false,
      rootSpeakerId: '',
      speechVectorId: '',
    });

    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('You')).toBeInTheDocument();
  });

  it('renders retroactive entries with catch-up badge', () => {
    useTranscriptStore.getState().addEntry({
      id: '1',
      speakerRole: 'candidate',
      text: 'Retroactive message',
      isFinal: true,
      confidence: 0.9,
      timestamp: '2024-01-01T00:00:00Z',
      channel: 'tab',
      isRetroactive: true,
      rootSpeakerId: '',
      speechVectorId: '',
    });

    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('catch-up')).toBeInTheDocument();
  });

  it('renders insight bar when analysis present', () => {
    useAnalysisStore.getState().addAnalysis({
      answerQuality: 4,
      onTrack: true,
      insight: 'Good use of data structures',
      redFlags: [],
      suggestionForInterviewer: '',
      hintSignal: 'none',
      timestamp: '2024-01-01T00:00:00Z',
      provider: 'mock',
      dimensions: null,
    });

    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('Good use of data structures')).toBeInTheDocument();
    expect(screen.getByText('4/5')).toBeInTheDocument();
  });
});

// ── HintPanel ─────────────────────────────────────────────────────────

describe('HintPanel', () => {
  const handlers = {
    onRequestRuntimeHint: vi.fn(),
    onRegenerateRuntimeHint: vi.fn(),
    onAcknowledgeRuntimeHint: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useHintStore.getState().clear();
  });

  it('shows Generate Smart Hint button', () => {
    render(<HintPanel {...handlers} />);
    expect(screen.getByText('Generate Smart Hint')).toBeInTheDocument();
  });

  it('calls onRequestRuntimeHint when button clicked', () => {
    render(<HintPanel {...handlers} />);
    fireEvent.click(screen.getByText('Generate Smart Hint'));
    expect(handlers.onRequestRuntimeHint).toHaveBeenCalledOnce();
  });

  const makeRuntimeHint = (overrides: Partial<import('@/stores/hint-store').RuntimeHintEntry> = {}) => ({
    hintId: 'rh1',
    questionId: 'q1',
    text: 'Consider using a hash map for O(1) lookups',
    directness: 'moderate' as const,
    isRegeneration: false,
    regenerationReason: null,
    generationNumber: 1,
    latencyMs: 500,
    provider: 'mock',
    timestamp: '2024-01-01T00:00:00Z',
    feedback: null,
    ...overrides,
  });

  it('renders active runtime hint with directness badge', () => {
    useHintStore.getState().setActiveRuntimeHint(makeRuntimeHint());

    render(<HintPanel {...handlers} />);
    expect(screen.getByText('Moderate')).toBeInTheDocument();
    const matches = screen.getAllByText('Consider using a hash map for O(1) lookups');
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('shows feedback buttons for hints without feedback', () => {
    useHintStore.getState().setActiveRuntimeHint(makeRuntimeHint({
      text: 'Think about the data structure',
      directness: 'subtle',
    }));

    render(<HintPanel {...handlers} />);
    expect(screen.getByText('Well received')).toBeInTheDocument();
    expect(screen.getByText('Partially')).toBeInTheDocument();
    expect(screen.getByText('Not understood')).toBeInTheDocument();
  });

  it('calls onAcknowledgeRuntimeHint with correct quality', () => {
    useHintStore.getState().setActiveRuntimeHint(makeRuntimeHint({
      text: 'Hint text',
      directness: 'direct',
    }));

    render(<HintPanel {...handlers} />);
    fireEvent.click(screen.getByText('Well received'));
    expect(handlers.onAcknowledgeRuntimeHint).toHaveBeenCalledWith('rh1', 'well_received');
  });

  it('shows feedback badge after acknowledgement', () => {
    useHintStore.getState().setActiveRuntimeHint(makeRuntimeHint({
      text: 'Hint text',
      feedback: 'well_received',
    }));

    render(<HintPanel {...handlers} />);
    expect(screen.getByText('well received')).toBeInTheDocument();
  });

  it('shows Regenerate button', () => {
    useHintStore.getState().setActiveRuntimeHint(makeRuntimeHint({
      text: 'Some hint',
      directness: 'subtle',
    }));

    render(<HintPanel {...handlers} />);
    expect(screen.getByText('Regenerate')).toBeInTheDocument();
  });
});

// ── ScoreRings ────────────────────────────────────────────────────────

describe('ScoreRings', () => {
  beforeEach(() => {
    useAnalysisStore.getState().clear();
  });

  it('renders all 4 score rings', () => {
    render(<ScoreRings />);
    expect(screen.getByLabelText(/Problem Solving/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Communication/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Code Quality/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Hint Response/)).toBeInTheDocument();
  });

  it('shows dashes for zero scores', () => {
    render(<ScoreRings />);
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBe(4);
  });

  it('shows score values when set', () => {
    useAnalysisStore.getState().updateScores({
      problemSolving: 4.2,
      communication: 3.5,
      codeQuality: 4.0,
      hintResponsiveness: 3.8,
    });

    render(<ScoreRings />);
    expect(screen.getByText('4.2')).toBeInTheDocument();
    expect(screen.getByText('3.5')).toBeInTheDocument();
    expect(screen.getByText('4.0')).toBeInTheDocument();
    expect(screen.getByText('3.8')).toBeInTheDocument();
  });
});

// ── SpeakersPanel ─────────────────────────────────────────────────────

describe('SpeakersPanel', () => {
  const mockSend = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    useSpeakerStore.getState().clear();
  });

  it('renders the Speakers heading', () => {
    render(withWS(<SpeakersPanel />, mockSend));
    expect(screen.getByText('Speakers')).toBeInTheDocument();
  });

  it('renders root speakers from store', () => {
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
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
      },
      speech_vectors: {},
    });

    render(withWS(<SpeakersPanel />, mockSend));
    expect(screen.getByText('Interviewer')).toBeInTheDocument();
    expect(screen.getByText('Candidate')).toBeInTheDocument();
  });

  it('shows + Add button', () => {
    render(withWS(<SpeakersPanel />, mockSend));
    expect(screen.getByText('+ Add')).toBeInTheDocument();
  });

  it('shows create form when + Add clicked', () => {
    render(withWS(<SpeakersPanel />, mockSend));
    fireEvent.click(screen.getByText('+ Add'));
    expect(screen.getByPlaceholderText('Speaker name')).toBeInTheDocument();
  });

  it('sends create_root_speaker message on form submit', () => {
    render(withWS(<SpeakersPanel />, mockSend));
    fireEvent.click(screen.getByText('+ Add'));

    const input = screen.getByPlaceholderText('Speaker name');
    fireEvent.change(input, { target: { value: 'John (PM)' } });
    fireEvent.click(screen.getByText('Add'));

    expect(mockSend).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'create_root_speaker',
        label: 'John (PM)',
      }),
    );
  });

  it('does not show delete button on default speakers', () => {
    useSpeakerStore.getState().applySnapshot({
      root_speakers: {
        'rs-default-interviewer': {
          id: 'rs-default-interviewer',
          label: 'Interviewer',
          color: '#818cf8',
          is_default: true,
          speech_vector_ids: [],
        },
      },
      speech_vectors: {},
    });

    render(withWS(<SpeakersPanel />, mockSend));
    expect(screen.queryByTitle('Delete speaker')).not.toBeInTheDocument();
  });
});
