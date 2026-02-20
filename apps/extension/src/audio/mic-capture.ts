/**
 * Microphone audio capture via getUserMedia.
 *
 * Captures the interviewer's microphone with echo cancellation,
 * routes through AudioWorklet for 16kHz 16-bit PCM chunks.
 */

export interface MicCaptureOptions {
  onChunk: (pcmData: ArrayBuffer, timestamp: number) => void;
  onError?: (error: string) => void;
  onStateChange?: (capturing: boolean) => void;
}

export class MicAudioCapture {
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private options: MicCaptureOptions;
  private capturing = false;

  constructor(options: MicCaptureOptions) {
    this.options = options;
  }

  get isCapturing(): boolean {
    return this.capturing;
  }

  async start(): Promise<void> {
    if (this.capturing) return;

    try {
      // Request microphone with echo cancellation and noise suppression
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
        video: false,
      });

      this.audioContext = new AudioContext();

      // Load the AudioWorklet processor
      const processorUrl = chrome.runtime.getURL('audio/audio-processor.js');
      await this.audioContext.audioWorklet.addModule(processorUrl);

      const source = this.audioContext.createMediaStreamSource(this.stream);
      this.workletNode = new AudioWorkletNode(this.audioContext, 'pcm-chunk-processor');

      this.workletNode.port.onmessage = (event) => {
        if (event.data.type === 'pcm-chunk') {
          this.options.onChunk(event.data.pcmData, event.data.timestamp);
        }
      };

      source.connect(this.workletNode);
      // Do NOT connect to destination — we don't want mic playback
      // this.workletNode.connect(this.audioContext.destination);

      this.capturing = true;
      this.options.onStateChange?.(true);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.options.onError?.(`Mic capture failed: ${message}`);
      await this.stop();
    }
  }

  async stop(): Promise<void> {
    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode = null;
    }

    if (this.audioContext) {
      await this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }

    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }

    this.capturing = false;
    this.options.onStateChange?.(false);
  }
}
