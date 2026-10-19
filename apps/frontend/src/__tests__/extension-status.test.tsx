import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { usePanelStore } from '@/stores/panel-store';
import * as api from '@/api/client';

vi.mock('@/hooks/useWebSocket', () => ({ useWebSocket: () => ({ send: vi.fn() }) }));
vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  getSession: vi.fn(),
  getSessionState: vi.fn(() => new Promise(() => {})),
  createPairingToken: vi.fn(),
}));

const MEET = 'https://meet.google.com/abc-defg-hij';

/** The extension end: the dashboard port, and the one-click pairing message. */
function fakeExtension() {
  const listeners: Array<(m: unknown) => void> = [];
  const port = {
    onMessage: { addListener: (cb: (m: unknown) => void) => listeners.push(cb) },
    onDisconnect: { addListener: vi.fn() },
    postMessage: vi.fn(),
    disconnect: vi.fn(),
  };
  const sendMessage = vi.fn((_id: string, _m: unknown, cb: (r: unknown) => void) => cb({ ok: true }));
  (globalThis as { chrome?: unknown }).chrome = { runtime: { connect: vi.fn(() => port), sendMessage } };
  return { sendMessage, report: (m: unknown) => act(() => listeners.forEach((cb) => cb(m))) };
}

function renderLiveSession() {
  return render(
    <MemoryRouter initialEntries={['/session/s1']}>
      <Routes>
        <Route path="/session/:sessionId" element={<LiveSessionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const status = (sessionId: string | null, capturing: boolean) => ({
  type: 'extension-status',
  sessionId,
  connected: true,
  capturing,
});

describe('the Connect extension strip follows what the extension reports', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_EXTENSION_ID', 'ext-id');
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    usePanelStore.setState({ hidden: {} });
    vi.mocked(api.getSession).mockResolvedValue({ meeting_url: MEET } as Awaited<ReturnType<typeof api.getSession>>);
    vi.mocked(api.createPairingToken).mockResolvedValue({ pairing_token: 'tok' } as Awaited<
      ReturnType<typeof api.createPairingToken>
    >);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    delete (globalThis as { chrome?: unknown }).chrome;
  });

  it('connect, then capture: the button gives way to the capture status', async () => {
    const ext = fakeExtension();
    renderLiveSession();

    await screen.findByDisplayValue(MEET);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Connect extension' }));
    });
    expect(ext.sendMessage).toHaveBeenCalledWith('ext-id', expect.objectContaining({ type: 'pair', meetingUrl: MEET }), expect.any(Function));

    ext.report(status('s1', false));
    expect(screen.queryByRole('button', { name: 'Connect extension' })).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent(/extension connected\. on the meet tab, press alt\+shift\+i/i);

    ext.report(status('s1', true));
    expect(screen.getByRole('status')).toHaveTextContent('Extension is capturing the meeting.');
    expect(screen.queryByRole('button', { name: 'Connect extension' })).toBeNull();
  });

  it('after a reload, shows capture straight away when the extension is already on this session', () => {
    const ext = fakeExtension();
    renderLiveSession();
    ext.report(status('s1', true));
    expect(screen.getByRole('status')).toHaveTextContent('Extension is capturing the meeting.');
  });

  it('offers Connect when the extension is on another session, and Reconnect brings the form back', async () => {
    const ext = fakeExtension();
    renderLiveSession();
    await screen.findByDisplayValue(MEET);
    ext.report(status('other-session', true));
    expect(screen.getByRole('button', { name: 'Connect extension' })).toBeInTheDocument();

    ext.report(status('s1', true));
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(screen.getByRole('button', { name: 'Connect extension' })).toBeInTheDocument();
    expect(screen.getByLabelText('Google Meet link')).toHaveValue(MEET);
  });
});
