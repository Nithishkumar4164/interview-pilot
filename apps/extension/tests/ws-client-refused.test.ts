import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WSClient } from '../src/networking/ws-client';

class FakeSocket {
  static opened: FakeSocket[] = [];
  binaryType = '';
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.opened.push(this);
  }
  send(): void {}
  close(): void {}
}

async function openClient(events: ConstructorParameters<typeof WSClient>[1]) {
  const client = new WSClient(
    { baseUrl: 'ws://test', sessionId: 's1', endpoint: '/ws/audio', initialRetryMs: 10 },
    events,
  );
  client.connect();
  await vi.advanceTimersByTimeAsync(0); // buildUrl resolves
  FakeSocket.opened.at(-1)!.onopen?.();
  return client;
}

describe('WSClient when the server refuses the session', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.opened = [];
    vi.stubGlobal('WebSocket', FakeSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('stops on close code 1008 and reports why (session ended)', async () => {
    const onRefused = vi.fn();
    const client = await openClient({ onRefused });

    FakeSocket.opened[0].onclose?.({ code: 1008, reason: 'Session ended' });
    await vi.advanceTimersByTimeAsync(60_000);

    expect(onRefused).toHaveBeenCalledWith('Session ended');
    expect(client.connectionState).toBe('disconnected');
    expect(FakeSocket.opened).toHaveLength(1);
  });

  it('still reconnects after any other close', async () => {
    const onRefused = vi.fn();
    await openClient({ onRefused });

    FakeSocket.opened[0].onclose?.({ code: 1006, reason: '' });
    await vi.advanceTimersByTimeAsync(60_000);

    expect(onRefused).not.toHaveBeenCalled();
    expect(FakeSocket.opened.length).toBeGreaterThan(1);
  });
});
