import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CopilotPanel } from '@/components/dashboard/CopilotPanel';
import { cardFromPayload, useCopilotStore, type CopilotCard } from '@/stores/copilot-store';

const PROBE: CopilotCard = {
  id: 'sess:utt-3:probe',
  kind: 'probe',
  priority: 'suggest',
  title: 'Suggested follow-ups',
  body: 'complexity claim',
  items: ['Why is the lookup constant time?', 'What if the array has duplicates?'],
  probe: 'Why is the lookup constant time?',
  questionId: 'q-two-sum',
  source: 'llm',
  timestamp: '2026-01-01T00:00:00Z',
};

function renderPanel(onUse = vi.fn()) {
  render(<CopilotPanel onUseCard={onUse} />);
  return onUse;
}

describe('copilot cards', () => {
  beforeEach(() => {
    useCopilotStore.getState().clear();
    for (const kind of [...useCopilotStore.getState().mutedKinds]) useCopilotStore.getState().toggleMute(kind);
  });

  it('maps the backend payload and ignores unknown kinds', () => {
    const card = cardFromPayload(
      { id: 'c1', kind: 'probe', title: 't', items: ['a', 1], probe: 'a', question_id: 'q', source: 'rule' },
      '2026-01-01T00:00:00Z',
    );
    expect(card).toMatchObject({ id: 'c1', kind: 'probe', items: ['a'], source: 'rule', priority: 'suggest' });
    expect(cardFromPayload({ id: 'c2', kind: 'weather' }, '')).toBeNull();
    expect(cardFromPayload({ kind: 'probe' }, '')).toBeNull();
  });

  it('shows a probe card and reports it as asked', () => {
    useCopilotStore.getState().addCard(PROBE);
    const onUse = renderPanel();
    const note = screen.getByRole('note', { name: 'Follow-up' });
    expect(note.textContent).toContain(PROBE.items[1]);
    fireEvent.click(screen.getByRole('button', { name: 'Mark as asked' }));
    expect(onUse).toHaveBeenCalledWith(PROBE);
    expect(useCopilotStore.getState().cards).toEqual([]);
    // A resumed run re-sending the same card does not bring it back.
    useCopilotStore.getState().addCard(PROBE);
    expect(useCopilotStore.getState().cards).toEqual([]);
  });

  it('dismisses without reporting', () => {
    useCopilotStore.getState().addCard(PROBE);
    const onUse = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onUse).not.toHaveBeenCalled();
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('keeps one card per id, newest first', () => {
    useCopilotStore.getState().addCard(PROBE);
    useCopilotStore.getState().addCard({ ...PROBE, id: 'other', title: 'second' });
    useCopilotStore.getState().addCard({ ...PROBE, title: 'resent' });
    expect(useCopilotStore.getState().cards.map((c) => c.title)).toEqual(['resent', 'second']);
  });

  it('hides muted kinds', () => {
    useCopilotStore.getState().addCard(PROBE);
    useCopilotStore.getState().addCard({ ...PROBE, id: 'coach', kind: 'coaching', title: 'Leading question' });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Mute…' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Coaching' }));
    expect(screen.queryByRole('note', { name: 'Coaching' })).toBeNull();
    expect(screen.getByRole('note', { name: 'Follow-up' })).toBeTruthy();
  });
});

import { coverageFromPayload } from '@/stores/copilot-store';
import { usePlanStore } from '@/stores/plan-store';
import { useSessionStore } from '@/stores/session-store';

describe('rubric coverage', () => {
  beforeEach(() => {
    useCopilotStore.getState().clear();
    useSessionStore.getState().reset();
  });

  it('maps the backend snapshot', () => {
    const coverage = coverageFromPayload(
      { criteria: { problem_solving: { evidence: 3, status: 'solid' }, behavioral: { evidence: 0, status: 'none' } }, gaps: ['behavioral'] },
      't',
    );
    expect(coverage).toEqual({
      criteria: { problem_solving: { evidence: 3, status: 'solid' }, behavioral: { evidence: 0, status: 'none' } },
      gaps: ['behavioral'],
      timestamp: 't',
    });
    expect(coverageFromPayload({}, 't')).toBeNull();
  });

  it('shows chips and points at the upcoming question that fills the gap', () => {
    const q = (id: string, category: string) => ({
      id, text: `Question ${id}`, category, difficulty: 2, time_minutes: 10, alternates: [], hints: [],
      brute_force_approach: null, optimal_approaches: null, common_pitfalls: null,
    });
    usePlanStore.getState().setPlan({
      id: 'p', session_id: 's', version: 1, is_active: true, total_minutes: 30, created_at: '',
      phases: [
        { phase: 'coding', start_minute: 0, duration_minutes: 20, questions: [q('a', 'coding'), q('b', 'coding')] },
        { phase: 'behavioral', start_minute: 20, duration_minutes: 10, questions: [q('c', 'behavioral')] },
      ],
    });
    useSessionStore.getState().setCurrentQuestion(0, 3, 'Question a');
    useCopilotStore.getState().setCoverage({
      criteria: { problem_solving: { evidence: 2, status: 'thin' }, behavioral: { evidence: 0, status: 'none' } },
      gaps: ['behavioral'],
      timestamp: 't',
    });
    renderPanel();
    expect(screen.getByTitle('Problem solving: 2 evidence points').className).toContain('coverage-chip--thin');
    expect(screen.getByLabelText('Rubric coverage').textContent).toContain('Q3 covers it: Question c');
  });
});

import { scorecardFromPayload } from '@/stores/copilot-store';

describe('live scorecard', () => {
  beforeEach(() => useCopilotStore.getState().clear());

  it('maps notes and groups them by criterion with the quote', () => {
    const notes = scorecardFromPayload({
      notes: [
        { id: 'u1:note', criterion: 'problem_solving', quote: 'I would sort first', note: 'Reasonable start', quality: 3, source: 'copilot', at: 't' },
        { id: 'n1', criterion: 'behavioral', note: 'Owned the mistake', source: 'interviewer', at: 't' },
        { criterion: 'bad' },
      ],
    });
    expect(notes?.map((n) => n.id)).toEqual(['u1:note', 'n1']);
    useCopilotStore.getState().setScorecard(notes!);
    const onAdd = vi.fn();
    render(<CopilotPanel onUseCard={vi.fn()} onAddNote={onAdd} />);
    fireEvent.click(screen.getByRole('button', { name: /Scorecard/ }));
    const card = screen.getByLabelText('Scorecard');
    expect(card.textContent).toContain('Reasonable start (3/5)');
    expect(card.textContent).toContain('“I would sort first”');
    expect(card.textContent).toContain('you: Owned the mistake');
    fireEvent.change(screen.getByLabelText('Note criterion'), { target: { value: 'communication' } });
    fireEvent.change(screen.getByLabelText('Note text'), { target: { value: '  Clear walkthrough ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(onAdd).toHaveBeenCalledWith('communication', 'Clear walkthrough');
  });
});
