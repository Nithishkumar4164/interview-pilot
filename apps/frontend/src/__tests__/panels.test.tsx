import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { usePanelStore } from '@/stores/panel-store';

vi.mock('@/hooks/useWebSocket', () => ({ useWebSocket: () => ({ send: vi.fn() }) }));
vi.mock('@/hooks/useExtensionChannel', () => ({ useExtensionChannel: () => ({ sendMeetControl: vi.fn() }) }));
vi.mock('@/components/auth/ConnectExtension', () => ({ ConnectExtension: () => null }));
vi.mock('@/api/client', async (importOriginal) => {
  const pending = () => new Promise(() => {});
  return {
    ...(await importOriginal<typeof import('@/api/client')>()),
    getSession: vi.fn(pending),
    getSessionState: vi.fn(pending),
  };
});

function renderLiveSession() {
  return render(
    <MemoryRouter initialEntries={['/session/s1']}>
      <Routes>
        <Route path="/session/:sessionId" element={<LiveSessionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// A hidden pane has no accessible name, so look it up by its label rather than its role.
function pane(name: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`section[aria-label="${name}"]`);
  if (!el) throw new Error(`No pane labelled ${name}`);
  return el;
}
const column = (name: string) => pane(name).parentElement!;

function openPanesMenu() {
  fireEvent.click(screen.getByRole('button', { name: /^panes/i }));
  return screen.getByRole('group', { name: 'Panes' });
}

describe('showing and hiding live-session panes', () => {
  beforeEach(() => {
    // jsdom has no layout; the transcript scrolls to its latest line.
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    usePanelStore.setState({ hidden: {} });
  });

  it("hides a pane from its title and shows it again from the Panes menu, keeping what's in it", () => {
    renderLiveSession();
    const questions = pane('Questions');
    fireEvent.change(within(questions).getByLabelText('Topic'), { target: { value: 'rate limiting' } });

    fireEvent.click(screen.getByRole('button', { name: 'Hide Questions' }));
    expect(questions).not.toBeVisible();
    // Its column had nothing else in it, so the column goes too.
    expect(column('Questions')).not.toBeVisible();
    expect(screen.getByRole('button', { name: /^panes \(1 hidden\)/i })).toBeInTheDocument();

    const menu = openPanesMenu();
    const box = within(menu).getByRole('checkbox', { name: 'Questions' });
    expect(box).not.toBeChecked();
    fireEvent.click(box);

    expect(pane('Questions')).toBeVisible();
    expect(column('Questions')).toBeVisible();
    expect(within(pane('Questions')).getByLabelText('Topic')).toHaveValue('rate limiting');
  });

  it('drops the right column only when all of its panes are hidden, and lets the next column fill the width', () => {
    renderLiveSession();
    const right = column('Copilot');
    const menu = openPanesMenu();

    for (const name of ['Copilot', 'Speakers', 'Scores', 'Flags']) {
      fireEvent.click(within(menu).getByRole('checkbox', { name }));
    }
    expect(right).toBeVisible();
    fireEvent.click(within(menu).getByRole('checkbox', { name: 'Smart Hints' }));
    expect(right).not.toBeVisible();

    // With the transcript hidden too, the Questions column takes the spare width.
    fireEvent.click(within(menu).getByRole('checkbox', { name: 'Transcript' }));
    expect(column('Live Transcript')).not.toBeVisible();
    expect(column('Questions')).toHaveClass('live-session__column--fill');
  });

  it('offers to show everything when every pane is hidden, and remembers hidden panes across reloads', () => {
    renderLiveSession();
    const menu = openPanesMenu();
    for (const box of within(menu).getAllByRole('checkbox')) fireEvent.click(box);

    expect(JSON.parse(localStorage.getItem('live_panes_hidden') ?? '{}')).toEqual({
      transcript: true,
      plan: true,
      copilot: true,
      speakers: true,
      scores: true,
      flags: true,
      hints: true,
    });
    expect(screen.getByText('All panes are hidden.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all panes' }));
    expect(pane('Live Transcript')).toBeVisible();
    expect(localStorage.getItem('live_panes_hidden')).toBe('{}');
  });
});
