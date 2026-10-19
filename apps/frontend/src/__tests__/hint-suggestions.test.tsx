import { describe, expect, it, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { HintPanel } from '@/components/dashboard/HintPanel';
import { useHintStore } from '@/stores/hint-store';

const SUGGESTION = {
  id: 'sess:utt-3:hint',
  questionId: 'q-two-sum',
  text: 'Ask what data structure gives constant-time lookups.',
  directness: 'subtle',
  receivedAt: '2026-01-01T00:00:00Z',
};

function renderPanel(onUse = vi.fn()) {
  render(
    <HintPanel
      onUseSuggestion={onUse}
      onRequestRuntimeHint={vi.fn()}
      onRegenerateRuntimeHint={vi.fn()}
      onAcknowledgeRuntimeHint={vi.fn()}
    />,
  );
  return onUse;
}

describe('suggested hints', () => {
  beforeEach(() => useHintStore.getState().clear());

  it('shows a drafted hint and hands it over on Use', () => {
    useHintStore.getState().addSuggestion(SUGGESTION);
    const onUse = renderPanel();
    expect(screen.getByRole('note', { name: 'Suggested hint' }).textContent).toContain(SUGGESTION.text);
    fireEvent.click(screen.getByRole('button', { name: 'Use' }));
    expect(onUse).toHaveBeenCalledWith(SUGGESTION);
    expect(useHintStore.getState().suggestions).toEqual([]);
  });

  it('dismisses without using', () => {
    useHintStore.getState().addSuggestion(SUGGESTION);
    const onUse = renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onUse).not.toHaveBeenCalled();
    expect(screen.queryByRole('note', { name: 'Suggested hint' })).toBeNull();
  });

  it('keeps one card per draft id', () => {
    useHintStore.getState().addSuggestion(SUGGESTION);
    useHintStore.getState().addSuggestion({ ...SUGGESTION, text: 'resent' });
    expect(useHintStore.getState().suggestions.map((s) => s.text)).toEqual(['resent']);
  });
});
