/**
 * IndexedDB ring buffer for offline resilience.
 *
 * Stores audio chunks and code snapshots when the WebSocket is disconnected.
 * On reconnect, drains chronologically with `buffered: true` flag and
 * original timestamps.
 *
 * Capacity:
 * - Audio: ~10 min at 16kHz 16-bit PCM, 250ms chunks → ~2400 chunks (~9.6MB)
 * - Code: 100 snapshots
 */

import { openDB, type IDBPDatabase } from 'idb';

const DB_NAME = 'interview-pilot-buffer';
const DB_VERSION = 1;
const AUDIO_STORE = 'audio-chunks';
const CODE_STORE = 'code-snapshots';

const MAX_AUDIO_CHUNKS = 2400; // ~10 min of 250ms chunks
const MAX_CODE_SNAPSHOTS = 100;

export interface BufferedAudioChunk {
  id?: number; // auto-increment
  pcmData: ArrayBuffer;
  channel: 'tab' | 'mic';
  timestamp: number;
  sampleRate: number;
}

export interface BufferedCodeSnapshot {
  id?: number; // auto-increment
  code: string;
  language: string;
  source: string;
  diff: string | null;
  timestamp: number;
}

export interface BufferStats {
  audioChunks: number;
  codeSnapshots: number;
  oldestAudioTimestamp: number | null;
  newestAudioTimestamp: number | null;
  estimatedAudioDurationMs: number;
  estimatedAudioSizeBytes: number;
}

export class OfflineBuffer {
  private db: IDBPDatabase | null = null;

  async open(): Promise<void> {
    if (this.db) return;

    this.db = await openDB(DB_NAME, DB_VERSION, {
      upgrade(db) {
        // Audio chunks store with auto-incrementing key
        if (!db.objectStoreNames.contains(AUDIO_STORE)) {
          const audioStore = db.createObjectStore(AUDIO_STORE, {
            keyPath: 'id',
            autoIncrement: true,
          });
          audioStore.createIndex('timestamp', 'timestamp');
        }

        // Code snapshots store with auto-incrementing key
        if (!db.objectStoreNames.contains(CODE_STORE)) {
          const codeStore = db.createObjectStore(CODE_STORE, {
            keyPath: 'id',
            autoIncrement: true,
          });
          codeStore.createIndex('timestamp', 'timestamp');
        }
      },
    });
  }

  private ensureDb(): IDBPDatabase {
    if (!this.db) {
      throw new Error('OfflineBuffer not opened. Call open() first.');
    }
    return this.db;
  }

  // ── Audio Chunks ──────────────────────────────────────────────────────

  async addAudioChunk(chunk: Omit<BufferedAudioChunk, 'id'>): Promise<void> {
    const db = this.ensureDb();

    // Enforce ring buffer: if at capacity, remove oldest
    const count = await db.count(AUDIO_STORE);
    if (count >= MAX_AUDIO_CHUNKS) {
      const tx = db.transaction(AUDIO_STORE, 'readwrite');
      const store = tx.objectStore(tx.objectStoreNames[0]);
      const cursor = await store.index('timestamp').openCursor();
      if (cursor) {
        await cursor.delete();
      }
      await tx.done;
    }

    await db.add(AUDIO_STORE, chunk);
  }

  async getAudioChunks(): Promise<BufferedAudioChunk[]> {
    const db = this.ensureDb();
    return db.getAllFromIndex(AUDIO_STORE, 'timestamp');
  }

  async clearAudioChunks(): Promise<void> {
    const db = this.ensureDb();
    await db.clear(AUDIO_STORE);
  }

  // ── Code Snapshots ────────────────────────────────────────────────────

  async addCodeSnapshot(snapshot: Omit<BufferedCodeSnapshot, 'id'>): Promise<void> {
    const db = this.ensureDb();

    // Enforce ring buffer: if at capacity, remove oldest
    const count = await db.count(CODE_STORE);
    if (count >= MAX_CODE_SNAPSHOTS) {
      const tx = db.transaction(CODE_STORE, 'readwrite');
      const store = tx.objectStore(tx.objectStoreNames[0]);
      const cursor = await store.index('timestamp').openCursor();
      if (cursor) {
        await cursor.delete();
      }
      await tx.done;
    }

    await db.add(CODE_STORE, snapshot);
  }

  async getCodeSnapshots(): Promise<BufferedCodeSnapshot[]> {
    const db = this.ensureDb();
    return db.getAllFromIndex(CODE_STORE, 'timestamp');
  }

  async clearCodeSnapshots(): Promise<void> {
    const db = this.ensureDb();
    await db.clear(CODE_STORE);
  }

  // ── Stats ─────────────────────────────────────────────────────────────

  async getStats(): Promise<BufferStats> {
    const db = this.ensureDb();

    const audioCount = await db.count(AUDIO_STORE);
    const codeCount = await db.count(CODE_STORE);

    let oldestAudio: number | null = null;
    let newestAudio: number | null = null;

    if (audioCount > 0) {
      const oldestCursor = await db
        .transaction(AUDIO_STORE)
        .objectStore(AUDIO_STORE)
        .index('timestamp')
        .openCursor();
      if (oldestCursor) {
        oldestAudio = oldestCursor.value.timestamp;
      }

      const newestCursor = await db
        .transaction(AUDIO_STORE)
        .objectStore(AUDIO_STORE)
        .index('timestamp')
        .openCursor(null, 'prev');
      if (newestCursor) {
        newestAudio = newestCursor.value.timestamp;
      }
    }

    return {
      audioChunks: audioCount,
      codeSnapshots: codeCount,
      oldestAudioTimestamp: oldestAudio,
      newestAudioTimestamp: newestAudio,
      estimatedAudioDurationMs:
        oldestAudio !== null && newestAudio !== null ? newestAudio - oldestAudio : 0,
      // 16kHz * 16-bit * 250ms = 16000 bytes per chunk
      estimatedAudioSizeBytes: audioCount * 8000,
    };
  }

  // ── Drain All ─────────────────────────────────────────────────────────

  /**
   * Get all buffered data (audio + code) sorted chronologically.
   * Call clear methods after successful drain.
   */
  async drainAll(): Promise<{
    audioChunks: BufferedAudioChunk[];
    codeSnapshots: BufferedCodeSnapshot[];
  }> {
    const [audioChunks, codeSnapshots] = await Promise.all([
      this.getAudioChunks(),
      this.getCodeSnapshots(),
    ]);

    return { audioChunks, codeSnapshots };
  }

  async clearAll(): Promise<void> {
    await Promise.all([this.clearAudioChunks(), this.clearCodeSnapshots()]);
  }

  async close(): Promise<void> {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }
}
