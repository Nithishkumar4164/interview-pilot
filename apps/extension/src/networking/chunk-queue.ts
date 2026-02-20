/**
 * Priority queue for draining buffered chunks on reconnect.
 *
 * Drains chronologically with rate limiting to avoid overwhelming
 * the backend after a reconnect.
 */

export interface QueuedChunk {
  data: ArrayBuffer;
  timestamp: number;
  type: 'audio' | 'code';
}

export interface DrainOptions {
  /** Maximum chunks to send per second (default: 20) */
  chunksPerSecond?: number;
  /** Callback invoked for each chunk */
  onChunk: (chunk: QueuedChunk) => boolean;
  /** Callback when drain completes */
  onComplete?: (sentCount: number, failedCount: number) => void;
}

export class ChunkQueue {
  private queue: QueuedChunk[] = [];
  private draining = false;
  private drainTimer: ReturnType<typeof setInterval> | null = null;

  get size(): number {
    return this.queue.length;
  }

  get isDraining(): boolean {
    return this.draining;
  }

  enqueue(chunk: QueuedChunk): void {
    this.queue.push(chunk);
  }

  enqueueAll(chunks: QueuedChunk[]): void {
    this.queue.push(...chunks);
    // Sort chronologically
    this.queue.sort((a, b) => a.timestamp - b.timestamp);
  }

  clear(): void {
    this.queue = [];
    this.stopDrain();
  }

  /**
   * Start draining the queue with rate limiting.
   * Sends chunks chronologically via the onChunk callback.
   */
  startDrain(options: DrainOptions): void {
    if (this.draining || this.queue.length === 0) {
      options.onComplete?.(0, 0);
      return;
    }

    this.draining = true;
    const chunksPerSecond = options.chunksPerSecond ?? 20;
    const intervalMs = Math.ceil(1000 / chunksPerSecond);
    let sentCount = 0;
    let failedCount = 0;

    this.drainTimer = setInterval(() => {
      if (this.queue.length === 0) {
        this.stopDrain();
        options.onComplete?.(sentCount, failedCount);
        return;
      }

      const chunk = this.queue.shift()!;
      const sent = options.onChunk(chunk);
      if (sent) {
        sentCount++;
      } else {
        failedCount++;
        // Put it back at the front if send failed
        this.queue.unshift(chunk);
        this.stopDrain();
        options.onComplete?.(sentCount, failedCount);
      }
    }, intervalMs);
  }

  stopDrain(): void {
    this.draining = false;
    if (this.drainTimer !== null) {
      clearInterval(this.drainTimer);
      this.drainTimer = null;
    }
  }
}
