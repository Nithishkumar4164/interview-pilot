import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { useExtensionChannel, type MeetControlsState } from '@/hooks/useExtensionChannel';
import { SessionBar } from '@/components/dashboard/SessionBar';
import { useSessionStore } from '@/stores/session-store';

const IN_CALL: MeetControlsState = { available: true, mic: 'on', camera: 'off', presenting: false, canPresent: true };

/** A stand-in for the extension end of the 'dashboard' port. */
function fakeExtension() {
  const listeners: Array<(m: unknown) => void> = [];
  const port = {
    onMessage: { addListener: (cb: (m: unknown) => void) => listeners.push(cb) },
    onDisconnect: { addListener: vi.fn() },
    postMessage: vi.fn(),
    disconnect: vi.fn(),
  };
  const connect = vi.fn(() => port);
  (globalThis as { chrome?: unknown }).chrome = { runtime: { connect } };
  return { port, connect, send: (m: unknown) => listeners.forEach((cb) => cb(m)) };
}

describe('meeting controls from the Interview Pilot tab', () => {
  beforeEach(() => vi.stubEnv('VITE_EXTENSION_ID', 'ext-id'));
  afterEach(() => {
    vi.unstubAllEnvs();
    delete (globalThis as { chrome?: unknown }).chrome;
  });

  it("relays Meet's state from the extension and sends controls back on the same port", () => {
    const ext = fakeExtension();
    const onMeetControls = vi.fn();
    const { result } = renderHook(() => useExtensionChannel('s-1', { onStream: vi.fn(), onMeetControls }));

    expect(ext.connect).toHaveBeenCalledWith('ext-id', { name: 'dashboard' });
    act(() => ext.send({ type: 'meet-controls', state: IN_CALL }));
    expect(onMeetControls).toHaveBeenCalledWith(IN_CALL);

    act(() => result.current.sendMeetControl('toggle-mic'));
    expect(ext.port.postMessage).toHaveBeenLastCalledWith({ type: 'meet-control', action: 'toggle-mic' });
  });

  it('shows Meet state in the session bar and sends each control', () => {
    useSessionStore.getState().setStatus('live');
    const onMeetControl = vi.fn();
    const bar = (controls: MeetControlsState | null) => (
      <SessionBar
        onPause={vi.fn()}
        onResume={vi.fn()}
        onEnd={vi.fn()}
        onToggleVideo={vi.fn()}
        videoActive={false}
        meetControls={controls}
        onMeetControl={onMeetControl}
      />
    );
    const { rerender } = render(bar(IN_CALL));

    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    fireEvent.click(screen.getByRole('button', { name: 'Camera on' }));
    fireEvent.click(screen.getByRole('button', { name: 'Present' }));
    expect(onMeetControl.mock.calls.map((c) => c[0])).toEqual(['toggle-mic', 'toggle-camera', 'present']);

    // Meet changed (muted there, presenting): the bar follows Meet.
    rerender(bar({ ...IN_CALL, mic: 'off', presenting: true }));
    expect(screen.getByRole('button', { name: 'Unmute' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Presenting' })).toBeInTheDocument();

    // No Meet tab: no controls.
    rerender(bar({ available: false, mic: null, camera: null, presenting: false, canPresent: false }));
    expect(screen.queryByRole('group', { name: 'Meeting controls' })).toBeNull();
  });
});
