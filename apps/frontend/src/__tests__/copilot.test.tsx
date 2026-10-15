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
