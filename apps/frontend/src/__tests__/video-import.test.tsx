import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { usePanelStore } from '@/stores/panel-store';

const extension = vi.hoisted(() => ({ onStream: null as ((stream: MediaStream) => void) | null }));

vi.mock('@/hooks/useWebSocket', () => ({ useWebSocket: () => ({ send: vi.fn() }) }));
vi.mock('@/hooks/useExtensionChannel', () => ({
  useExtensionChannel: (_id: string | null, handlers: { onStream: (stream: MediaStream) => void }) => {
    extension.onStream = handlers.onStream;
    return { sendMeetControl: vi.fn() };
  },
}));
vi.mock('@/components/auth/ConnectExtension', () => ({ ConnectExtension: () => null }));
vi.mock('@/api/client', async (importOriginal) => {
  const pending = () => new Promise(() => {});
  return {
    ...(await importOriginal<typeof import('@/api/client')>()),
    getSession: vi.fn(pending),
    getSessionState: vi.fn(pending),
  };
});

function fakeStream(): MediaStream {
  const track = { addEventListener: vi.fn(), removeEventListener: vi.fn(), stop: vi.fn() };
  return { getVideoTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream;
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

/** The overlay is pinned when it sits in the transcript column with the Undock button. */
function expectPinned() {
  const undock = screen.getByRole('button', { name: 'Undock overlay' });
  const overlay = undock.closest('.video-overlay')!;
  expect(overlay).toHaveClass('video-overlay--docked');
  expect(overlay.parentElement).toHaveClass('live-session__left');
  expect(screen.queryByRole('button', { name: 'Dock overlay' })).toBeNull();
}

describe('imported meeting video', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    usePanelStore.setState({ hidden: {} });
    // jsdom has no media element playback.
    HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  });

  it('is pinned above the transcript when imported with Import Video', async () => {
    (globalThis as { CaptureController?: unknown }).CaptureController = class {
      setFocusBehavior() {}
    };
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getDisplayMedia: vi.fn(async () => fakeStream()) },
    });
    renderLiveSession();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /import video/i }));
    });
    expectPinned();
  });

  it('is pinned above the transcript when the extension sends the Meet tab', () => {
    renderLiveSession();
    act(() => extension.onStream!(fakeStream()));
    expectPinned();
  });
});
