import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { flagsFromPayload } from '@/lib/flags';
import { FlagsPanel } from '@/components/dashboard/FlagsPanel';
import { useAnalysisStore } from '@/stores/analysis-store';

// Payloads exactly as the backend sends them on the `red_flag` channel.
const ANALYSIS_FLAGS = { flags: ['Claims ownership but vague on details', 'Avoided the question'], timestamp: '2026-01-01T00:00:01Z' };
const CONTRADICTION = {
  type: 'contradiction',
  claim: 'I have used Kafka for about four years, mostly on the ingestion side.',
  new_statement: "Actually I've never used Kafka Connect or MirrorMaker myself.",
  explanation: "Candidate previously said 'I have used' but now says 'I've never'",
  suggested_probe: 'Could you clarify your experience level with this technology?',
  confidence: 0.8,
  timestamp: '2026-01-01T00:00:02Z',
};

describe('flagsFromPayload', () => {
  it('turns an analysis red-flag message into one flag per entry', () => {
    const flags = flagsFromPayload(ANALYSIS_FLAGS, 'fallback');
    expect(flags.map((f) => [f.kind, f.text, f.timestamp])).toEqual([
      ['red_flag', 'Claims ownership but vague on details', '2026-01-01T00:00:01Z'],
      ['red_flag', 'Avoided the question', '2026-01-01T00:00:01Z'],
    ]);
  });

  it('keeps a contradiction with both statements and the probe', () => {
    const [flag] = flagsFromPayload(CONTRADICTION, 'fallback');
    expect(flag).toMatchObject({
      kind: 'contradiction',
      text: CONTRADICTION.explanation,
      claim: CONTRADICTION.claim,
      newStatement: CONTRADICTION.new_statement,
      probe: CONTRADICTION.suggested_probe,
      confidence: 0.8,
    });
  });

  it('reads a code mismatch', () => {
    const [flag] = flagsFromPayload(
      { type: 'code_mismatch', explanation: 'Says O(n) but the code nests two loops', suggested_probe: 'Walk me through the inner loop.' },
      't',
    );
    expect([flag.kind, flag.text, flag.probe, flag.timestamp]).toEqual([
      'code_mismatch',
      'Says O(n) but the code nests two loops',
      'Walk me through the inner loop.',
      't',
    ]);
  });
});

describe('FlagsPanel', () => {
  beforeEach(() => useAnalysisStore.getState().clear());

  it('shows flags newest first with contradiction details', () => {
    useAnalysisStore.getState().addRedFlags(flagsFromPayload(ANALYSIS_FLAGS, 't'));
    useAnalysisStore.getState().addRedFlags(flagsFromPayload(CONTRADICTION, 't'));
    render(<FlagsPanel />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toContain('Contradiction');
    expect(items[0].textContent).toContain(CONTRADICTION.claim);
    expect(items[0].textContent).toContain(CONTRADICTION.new_statement);
    expect(items[0].textContent).toContain(CONTRADICTION.suggested_probe);
    expect(items[2].textContent).toContain('Claims ownership but vague on details');
  });

  it('says so when there are none', () => {
    render(<FlagsPanel />);
    expect(screen.getByText('No flags yet.')).toBeTruthy();
  });
});
