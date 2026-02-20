/**
 * AudioWorkletProcessor for converting raw audio to 16kHz 16-bit PCM chunks.
 *
 * This file is loaded as an AudioWorklet module inside an AudioContext.
 * It processes 128-sample input frames and accumulates them into 250ms chunks
 * (4000 samples at 16kHz) before posting the chunk to the main thread.
 *
 * NOTE: This code runs inside an AudioWorkletGlobalScope, NOT the main thread.
 */

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_DURATION_MS = 250;
const SAMPLES_PER_CHUNK = (TARGET_SAMPLE_RATE * CHUNK_DURATION_MS) / 1000; // 4000

class PCMChunkProcessor extends AudioWorkletProcessor {
  private buffer: Float32Array;
  private bufferOffset: number;
  private inputSampleRate: number;
  private resampleRatio: number;

  constructor() {
    super();
    this.buffer = new Float32Array(SAMPLES_PER_CHUNK);
    this.bufferOffset = 0;
    // sampleRate is available in AudioWorkletGlobalScope
    this.inputSampleRate = sampleRate;
    this.resampleRatio = TARGET_SAMPLE_RATE / this.inputSampleRate;
  }

  process(inputs: Float32Array[][], _outputs: Float32Array[][], _parameters: Record<string, Float32Array>): boolean {
    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }

    // Take first channel (mono)
    const channelData = input[0];
    if (!channelData) {
      return true;
    }

    // Simple linear resampling from input rate to 16kHz
    if (this.resampleRatio === 1) {
      // No resampling needed
      for (let i = 0; i < channelData.length; i++) {
        this.buffer[this.bufferOffset++] = channelData[i];
        if (this.bufferOffset >= SAMPLES_PER_CHUNK) {
          this.emitChunk();
        }
      }
    } else {
      // Resample: walk through input at the resampling ratio
      const outputSamples = Math.floor(channelData.length * this.resampleRatio);
      for (let i = 0; i < outputSamples; i++) {
        const srcIndex = i / this.resampleRatio;
        const srcIndexFloor = Math.floor(srcIndex);
        const srcIndexCeil = Math.min(srcIndexFloor + 1, channelData.length - 1);
        const frac = srcIndex - srcIndexFloor;

        // Linear interpolation
        const sample = channelData[srcIndexFloor] * (1 - frac) + channelData[srcIndexCeil] * frac;
        this.buffer[this.bufferOffset++] = sample;

        if (this.bufferOffset >= SAMPLES_PER_CHUNK) {
          this.emitChunk();
        }
      }
    }

    return true; // keep processor alive
  }

  private emitChunk(): void {
    // Convert float32 [-1, 1] to int16 [-32768, 32767] PCM
    const pcm16 = new Int16Array(SAMPLES_PER_CHUNK);
    for (let i = 0; i < SAMPLES_PER_CHUNK; i++) {
      const s = Math.max(-1, Math.min(1, this.buffer[i]));
      pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    this.port.postMessage(
      {
        type: 'pcm-chunk',
        pcmData: pcm16.buffer,
        sampleRate: TARGET_SAMPLE_RATE,
        timestamp: Date.now(),
      },
      [pcm16.buffer], // Transfer ownership for zero-copy
    );

    // Reset buffer
    this.buffer = new Float32Array(SAMPLES_PER_CHUNK);
    this.bufferOffset = 0;
  }
}

registerProcessor('pcm-chunk-processor', PCMChunkProcessor);
