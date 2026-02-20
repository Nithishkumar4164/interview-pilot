import { describe, it, expect } from 'vitest';
import {
  createAudioFrame,
  parseAudioHeader,
  AUDIO_HEADER_SIZE,
} from '../src/networking/ws-client';

describe('Audio Frame Protocol', () => {
  it('creates a frame with correct header size', () => {
    const pcm = new ArrayBuffer(8000); // 250ms of 16kHz 16-bit
    const frame = createAudioFrame(pcm, 'tab', Date.now(), 16000, false);

    expect(frame.byteLength).toBe(AUDIO_HEADER_SIZE + 8000);
  });

  it('round-trips tab channel metadata', () => {
    const pcm = new ArrayBuffer(100);
    const timestamp = 1708000000000;
    const frame = createAudioFrame(pcm, 'tab', timestamp, 16000, false);
    const parsed = parseAudioHeader(frame);

    expect(parsed.channel).toBe('tab');
    expect(parsed.timestamp).toBe(timestamp);
    expect(parsed.sampleRate).toBe(16000);
    expect(parsed.buffered).toBe(false);
    expect(parsed.pcmData.byteLength).toBe(100);
  });

  it('round-trips mic channel metadata', () => {
    const pcm = new ArrayBuffer(200);
    const timestamp = 1708000001234;
    const frame = createAudioFrame(pcm, 'mic', timestamp, 16000, true);
    const parsed = parseAudioHeader(frame);

    expect(parsed.channel).toBe('mic');
    expect(parsed.timestamp).toBe(timestamp);
    expect(parsed.sampleRate).toBe(16000);
    expect(parsed.buffered).toBe(true);
    expect(parsed.pcmData.byteLength).toBe(200);
  });

  it('preserves PCM data content', () => {
    const pcm = new ArrayBuffer(4);
    const view = new Uint8Array(pcm);
    view[0] = 0xde;
    view[1] = 0xad;
    view[2] = 0xbe;
    view[3] = 0xef;

    const frame = createAudioFrame(pcm, 'tab', Date.now());
    const parsed = parseAudioHeader(frame);
    const parsedView = new Uint8Array(parsed.pcmData);

    expect(parsedView[0]).toBe(0xde);
    expect(parsedView[1]).toBe(0xad);
    expect(parsedView[2]).toBe(0xbe);
    expect(parsedView[3]).toBe(0xef);
  });

  it('handles empty PCM data', () => {
    const pcm = new ArrayBuffer(0);
    const frame = createAudioFrame(pcm, 'tab', Date.now());

    expect(frame.byteLength).toBe(AUDIO_HEADER_SIZE);

    const parsed = parseAudioHeader(frame);
    expect(parsed.pcmData.byteLength).toBe(0);
  });
});
