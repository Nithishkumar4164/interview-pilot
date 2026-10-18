import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { InterviewPlanPanel } from '@/components/dashboard/InterviewPlanPanel';
import { usePlanStore } from '@/stores/plan-store';
import { useSessionStore } from '@/stores/session-store';
import { usePanelStore } from '@/stores/panel-store';
import * as api from '@/api/client';

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  suggestTopics: vi.fn(),
  generateQuestion: vi.fn(),
  logQuestion: vi.fn(),
}));

const HINTS = [
  { level: 1, text: 'What do you need to remember per element?' },
  { level: 2, text: 'A hash map from value to index.' },
  { level: 3, text: 'Look up target - x before storing x.' },
];

function draft(text: string): api.GeneratedQuestion {
  return {
    id: `draft-${text}`,
    text,
    category: 'coding',
    difficulty: 3,
    time_minutes: 20,
    alternates: [],
    hints: HINTS,
    brute_force_approach: null,
    optimal_approaches: [{ name: 'Optimal: hash map', data_structures: [], expected_complexity: { time: 'O(n)' }, key_steps: ['store complements'] }],
    common_pitfalls: ['Using an element twice'],
  };
}

/** The live page's handler: log the question, add it, make it current. */
async function useQuestion(category: string, topic: string | null, question: api.GeneratedQuestion) {
  const logged = await api.logQuestion('s-1', { category, topic, question });
  const index = usePlanStore.getState().addQuestion('s-1', logged);
  useSessionStore.getState().goToQuestion(index);
}

function renderPanel(onUseQuestion = vi.fn(useQuestion)) {
  render(
    <InterviewPlanPanel
      sessionId="s-1"
      focusAreas={['coding', 'system_design']}
      onUseQuestion={onUseQuestion}
      onAdvanceQuestion={vi.fn()}
      onGoToQuestion={vi.fn()}
      onRequestHint={vi.fn()}
      onAcknowledgeHint={vi.fn()}
      onRateCode={vi.fn()}
    />,
  );
  return onUseQuestion;
}

describe('generating questions during the interview', () => {
  beforeEach(() => {
    usePlanStore.getState().reset();
    useSessionStore.getState().reset();
    usePanelStore.setState({ collapsed: {} });
    vi.mocked(api.suggestTopics).mockReset().mockResolvedValue({
      category: 'coding',
      topics: [
        { concept: 'hashing', topic: 'Find two numbers that sum to a target.' },
        { concept: 'graphs', topic: 'Count islands in a grid.' },
      ],
    });
    vi.mocked(api.generateQuestion)
      .mockReset()
      .mockResolvedValueOnce({ category: 'coding', topic: null, question: draft('Two sum') })
      .mockResolvedValueOnce({ category: 'coding', topic: null, question: draft('Two sum, sorted input') });
    vi.mocked(api.logQuestion)
      .mockReset()
      .mockImplementation(async (_sid, data) => ({
        id: 'q-1',
        position: 0,
        category: data.category,
        topic: data.topic ?? null,
        asked_at: '2026-10-18T10:00:00Z',
        question: { ...data.question, id: 'q-1' },
      }));
  });

  it('suggests topics, regenerates a rejected draft, and logs the used one as the current question', async () => {
    renderPanel();

    // With nothing asked yet, the generator is open on the first focus area.
    expect(screen.getByLabelText('Category')).toHaveValue('coding');
    expect(api.suggestTopics).toHaveBeenCalledWith('s-1', 'coding');
    fireEvent.click(await screen.findByRole('button', { name: 'hashing' }));
    expect(screen.getByLabelText('Topic')).toHaveValue('hashing: Find two numbers that sum to a target.');

    fireEvent.click(screen.getByRole('button', { name: 'Generate question' }));
    const first = await screen.findByRole('article', { name: 'Draft question' });
    expect(within(first).getByText('Two sum')).toBeInTheDocument();
    expect(within(first).getByText('Hints (3)')).toBeInTheDocument();

    // Regenerate with a note: the rejected draft goes back so the next one differs.
    fireEvent.change(screen.getByLabelText('What should change?'), { target: { value: 'harder' } });
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(await screen.findByText('Two sum, sorted input')).toBeInTheDocument();
    expect(api.generateQuestion).toHaveBeenLastCalledWith('s-1', {
      category: 'coding',
      topic: 'hashing: Find two numbers that sum to a target.',
      rejected_drafts: ['Two sum'],
      feedback: 'harder',
    });

    fireEvent.click(screen.getByRole('button', { name: 'Use this question' }));

    // Logged once (the used draft only), and now the current question with its hint ladder.
    await waitFor(() => expect(api.logQuestion).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.logQuestion).mock.calls[0][1].question.text).toBe('Two sum, sorted input');
    expect(await screen.findByText('Q1 of 1')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Draft question' })).toBeNull();
    expect(screen.getByText('Nudge')).toBeInTheDocument();
    expect(usePlanStore.getState().questionnaire.map((q) => q.question.text)).toEqual(['Two sum, sorted input']);

    // The next question starts from the header button, with fresh suggestions (what was asked changed).
    fireEvent.click(screen.getByRole('button', { name: '+ New question' }));
    await waitFor(() => expect(api.suggestTopics).toHaveBeenCalledTimes(2));
  });

  it('keeps the draft when using it fails', async () => {
    vi.mocked(api.logQuestion).mockReset().mockRejectedValue(new Error('The interview has ended'));
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Generate question' }));
    await screen.findByText('Two sum');
    fireEvent.click(screen.getByRole('button', { name: 'Use this question' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The interview has ended');
    expect(screen.getByText('Two sum')).toBeInTheDocument();
  });
});
