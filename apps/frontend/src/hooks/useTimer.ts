/**
 * useTimer — interval-based elapsed time ticker for live sessions.
 *
 * Ticks every second when the session is 'live' and updates
 * the session store's elapsedMs.
 */
import { useEffect, useRef } from 'react';
import { useSessionStore } from '@/stores/session-store';

const TICK_INTERVAL_MS = 1000;

export function useTimer() {
  const status = useSessionStore((s) => s.status);
  const tickElapsed = useSessionStore((s) => s.tickElapsed);
  const lastTickRef = useRef<number>(0);

  useEffect(() => {
    if (status !== 'live') {
      lastTickRef.current = 0;
      return;
    }

    lastTickRef.current = Date.now();

    const intervalId = setInterval(() => {
      const now = Date.now();
      const delta = now - lastTickRef.current;
      lastTickRef.current = now;
      tickElapsed(delta);
    }, TICK_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [status, tickElapsed]);
}
