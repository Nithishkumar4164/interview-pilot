import { describe, it, expect, vi } from 'vitest';
import { ChunkQueue, type QueuedChunk } from '../src/networking/chunk-queue';

function makeChunk(timestamp: number, type: 'audio' | 'code' = 'audio'): QueuedChunk {
  return {
    data: new ArrayBuffer(100),
    timestamp,
    type,
  };
}

describe('ChunkQueue', () => {
  it('tracks queue size', () => {
    const queue = new ChunkQueue();
    expect(queue.size).toBe(0);

    queue.enqueue(makeChunk(1000));
    expect(queue.size).toBe(1);

    queue.enqueue(makeChunk(2000));
    expect(queue.size).toBe(2);
  });

  it('enqueueAll sorts chronologically', () => {
    const queue = new ChunkQueue();
    const chunks = [makeChunk(3000), makeChunk(1000), makeChunk(2000)];

    queue.enqueueAll(chunks);
    expect(queue.size).toBe(3);
  });

  it('clears the queue', () => {
    const queue = new ChunkQueue();
    queue.enqueue(makeChunk(1000));
    queue.enqueue(makeChunk(2000));

    queue.clear();
    expect(queue.size).toBe(0);
  });

  it('drains chunks via onChunk callback', () => {
    vi.useFakeTimers();
    const queue = new ChunkQueue();
    const sent: number[] = [];

    queue.enqueueAll([makeChunk(1000), makeChunk(2000), makeChunk(3000)]);

    queue.startDrain({
      chunksPerSecond: 1000, // fast for test
      onChunk: (chunk) => {
        sent.push(chunk.timestamp);
        return true;
      },
      onComplete: (sentCount, failedCount) => {
        expect(sentCount).toBe(3);
        expect(failedCount).toBe(0);
      },
    });

    // Advance timers to process all chunks
    vi.advanceTimersByTime(100);
    expect(sent).toEqual([1000, 2000, 3000]);
    expect(queue.size).toBe(0);

    vi.useRealTimers();
  });

  it('stops draining on send failure and puts chunk back', () => {
    vi.useFakeTimers();
    const queue = new ChunkQueue();
    let callCount = 0;

    queue.enqueueAll([makeChunk(1000), makeChunk(2000)]);

    queue.startDrain({
      chunksPerSecond: 1000,
      onChunk: () => {
        callCount++;
        if (callCount === 2) return false; // fail on second
        return true;
      },
      onComplete: (sentCount, failedCount) => {
        expect(sentCount).toBe(1);
        expect(failedCount).toBe(1);
      },
    });

    vi.advanceTimersByTime(100);
    // Second chunk should be put back
    expect(queue.size).toBe(1);

    vi.useRealTimers();
  });

  it('calls onComplete immediately if queue is empty', () => {
    const queue = new ChunkQueue();
    const onComplete = vi.fn();

    queue.startDrain({
      onChunk: () => true,
      onComplete,
    });

    expect(onComplete).toHaveBeenCalledWith(0, 0);
  });

  it('reports draining state', () => {
    vi.useFakeTimers();
    const queue = new ChunkQueue();
    queue.enqueue(makeChunk(1000));

    expect(queue.isDraining).toBe(false);

    queue.startDrain({
      chunksPerSecond: 1,
      onChunk: () => true,
    });

    expect(queue.isDraining).toBe(true);

    vi.advanceTimersByTime(2000);
    expect(queue.isDraining).toBe(false);

    vi.useRealTimers();
  });
});
