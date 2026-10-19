import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { flagsFromPayload } from '@/lib/flags';
import { FlagsPanel } from '@/components/dashboard/FlagsPanel';
import { useAnalysisStore } from '@/stores/analysis-store';

// The payload exactly as the backend sends it on the `red_flag` channel.
const ANALYSIS_FLAGS = { flags: ['Claims ownership but vague on details', 'Avoided the question'], timestamp: '2026-01-01T00:00:01Z' };

describe('flagsFromPayload', () => {
  it('turns an analysis red-flag message into one flag per entry', () => {
    const flags = flagsFromPayload(ANALYSIS_FLAGS, 'fallback');
    expect(flags.map((f) => [f.text, f.timestamp])).toEqual([
      ['Claims ownership but vague on details', '2026-01-01T00:00:01Z'],
      ['Avoided the question', '2026-01-01T00:00:01Z'],
    ]);
  });

  it('falls back to the message time and skips empty entries', () => {
    const flags = flagsFromPayload({ flags: ['  ', 'Evasive', 3] }, 't');
    expect(flags.map((f) => [f.text, f.timestamp])).toEqual([['Evasive', 't']]);
  });
});

describe('FlagsPanel', () => {
  beforeEach(() => useAnalysisStore.getState().clear());

  it('shows flags newest first', () => {
    useAnalysisStore.getState().addRedFlags(flagsFromPayload(ANALYSIS_FLAGS, 't'));
    render(<FlagsPanel />);
    const items = screen.getAllByRole('listitem');
    expect(items.map((i) => i.textContent)).toEqual([
      'Red flagAvoided the question',
      'Red flagClaims ownership but vague on details',
    ]);
  });

  it('says so when there are none', () => {
    render(<FlagsPanel />);
    expect(screen.getByText('No flags yet.')).toBeTruthy();
  });
});
