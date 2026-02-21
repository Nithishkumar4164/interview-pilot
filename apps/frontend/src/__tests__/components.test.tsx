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
    });

    render(withWS(<TranscriptPanel />));
    expect(screen.getByText('Good use of data structures')).toBeInTheDocument();
    expect(screen.getByText('4/5')).toBeInTheDocument();
  });
});

// ── HintPanel ─────────────────────────────────────────────────────────

describe('HintPanel', () => {
  const handlers = {
    onRequestHint: vi.fn(),
    onAcknowledge: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useHintStore.getState().clear();
  });

  it('shows "No active hint" when empty', () => {
    render(<HintPanel {...handlers} />);
    expect(screen.getByText('No active hint')).toBeInTheDocument();
  });

  it('shows Show Hint button when level < 3', () => {
    render(<HintPanel {...handlers} />);
    expect(screen.getByText(/Show Hint/)).toBeInTheDocument();
  });

  it('calls onRequestHint when button clicked', () => {
    render(<HintPanel {...handlers} />);
    fireEvent.click(screen.getByText(/Show Hint/));
    expect(handlers.onRequestHint).toHaveBeenCalledOnce();
  });

  it('renders active hint with level badge', () => {
    useHintStore.getState().setActiveHint({
      id: 'h1',
      questionId: 'q1',
      level: 2,
      text: 'Consider using a hash map for O(1) lookups',
      displayedAt: '2024-01-01T00:00:00Z',
      interviewerUsed: false,
      candidateResponseQuality: 'none',
      timeToRespondMs: null,
    });

    render(<HintPanel {...handlers} />);
    expect(screen.getByText('Guide')).toBeInTheDocument();
    // Text appears in both active hint card and timeline; use getAllByText
    const matches = screen.getAllByText('Consider using a hash map for O(1) lookups');
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('shows quality feedback buttons for unused hints', () => {
    useHintStore.getState().setActiveHint({
      id: 'h1',
      questionId: 'q1',
      level: 1,
      text: 'Think about the data structure',
      displayedAt: '2024-01-01T00:00:00Z',
      interviewerUsed: false,
      candidateResponseQuality: 'none',
      timeToRespondMs: null,
    });

    render(<HintPanel {...handlers} />);
    expect(screen.getByText('Got It')).toBeInTheDocument();
    expect(screen.getByText('Partially')).toBeInTheDocument();
    expect(screen.getByText('No Response')).toBeInTheDocument();
  });

  it('calls onAcknowledge with correct quality', () => {
    useHintStore.getState().setActiveHint({
      id: 'h1',
      questionId: 'q1',
      level: 1,
      text: 'Hint text',
      displayedAt: '2024-01-01T00:00:00Z',
      interviewerUsed: false,
      candidateResponseQuality: 'none',
      timeToRespondMs: null,
    });

    render(<HintPanel {...handlers} />);
    fireEvent.click(screen.getByText('Got It'));
    expect(handlers.onAcknowledge).toHaveBeenCalledWith('h1', 'full');
  });

  it('renders hint signal indicator', () => {
    useHintStore.getState().setHintSignal('guide');
    render(<HintPanel {...handlers} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/hint recommended/i)).toBeInTheDocument();
  });

  it('responds to H keyboard shortcut', () => {
    render(<HintPanel {...handlers} />);
    fireEvent.keyDown(document, { key: 'H' });
    expect(handlers.onRequestHint).toHaveBeenCalledOnce();
  });

  it('does not trigger H shortcut in input fields', () => {
    const { container } = render(
      <div>
        <input data-testid="input" />
        <HintPanel {...handlers} />
      </div>,
    );
    const input = container.querySelector('input')!;
    // Dispatch native event with input as target
    const event = new KeyboardEvent('keydown', { key: 'H', bubbles: true });
    Object.defineProperty(event, 'target', { value: input });
    document.dispatchEvent(event);
    expect(handlers.onRequestHint).not.toHaveBeenCalled();
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
