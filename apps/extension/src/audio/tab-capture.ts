/**
 * Tab audio capture via chrome.tabCapture API.
 *
 * Captures the active tab's audio, routes it through an AudioWorklet
 * to produce 16kHz 16-bit PCM chunks every 250ms.
 */

export interface TabCaptureOptions {
  onChunk: (pcmData: ArrayBuffer, timestamp: number) => void;
  onError?: (error: string) => void;
  onStateChange?: (capturing: boolean) => void;
}

export class TabAudioCapture {
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private options: TabCaptureOptions;
  private capturing = false;

  constructor(options: TabCaptureOptions) {
    this.options = options;
  }

  get isCapturing(): boolean {
    return this.capturing;
  }

  async start(): Promise<void> {
    if (this.capturing) return;

    try {
      // Request tab audio capture
      this.stream = await new Promise<MediaStream>((resolve, reject) => {
        chrome.tabCapture.capture(
          {
            audio: true,
            video: false,
          },
          (stream) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
              return;
            }
            if (!stream) {
              reject(new Error('Failed to capture tab audio: no stream returned'));
              return;
            }
            resolve(stream);
          },
        );
      });

      // Create AudioContext at the input sample rate (browser decides)
      this.audioContext = new AudioContext();

      // Load the AudioWorklet processor
      const processorUrl = chrome.runtime.getURL('audio/audio-processor.js');
      await this.audioContext.audioWorklet.addModule(processorUrl);

      // Connect: stream → source → worklet
      const source = this.audioContext.createMediaStreamSource(this.stream);
      this.workletNode = new AudioWorkletNode(this.audioContext, 'pcm-chunk-processor');

      this.workletNode.port.onmessage = (event) => {
        if (event.data.type === 'pcm-chunk') {
          this.options.onChunk(event.data.pcmData, event.data.timestamp);
        }
      };

      source.connect(this.workletNode);
      // Connect to destination to keep the pipeline alive (muted if needed)
      this.workletNode.connect(this.audioContext.destination);

      this.capturing = true;
      this.options.onStateChange?.(true);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.options.onError?.(`Tab capture failed: ${message}`);
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
