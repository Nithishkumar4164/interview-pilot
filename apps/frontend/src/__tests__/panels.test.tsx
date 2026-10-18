import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FlagsPanel } from '@/components/dashboard/FlagsPanel';
import { usePanelStore } from '@/stores/panel-store';

describe('collapsible panels', () => {
  beforeEach(() => {
    localStorage.clear();
    usePanelStore.setState({ collapsed: {} });
  });

  it('collapses to its header and expands again', () => {
    render(<FlagsPanel />);
    const toggle = screen.getByRole('button', { name: /flags/i });
    const panel = screen.getByRole('region', { name: 'Flags' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(panel).not.toHaveClass('is-collapsed');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(panel).toHaveClass('is-collapsed');
    // The header row stays; the CSS hides every other child of a collapsed panel.
    expect(toggle.closest('.panel-header')).not.toBeNull();

    fireEvent.click(toggle);
    expect(panel).not.toHaveClass('is-collapsed');
  });

  it('remembers collapsed panels across reloads', () => {
    usePanelStore.getState().toggle('flags');
    expect(JSON.parse(localStorage.getItem('live_panels_collapsed') ?? '{}')).toEqual({ flags: true });
  });
});
