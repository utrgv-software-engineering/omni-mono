/** Observe media tracks without routing or altering their audio. */
export function rmsLevel(samples: Float32Array): number {
  if (!samples.length) return 0;
  return Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
}
const visualLevel = (rms: number) => Math.min(1, Math.max(0, rms - 0.008) * 8);

export class VoiceAudioMeter {
  private context = new AudioContext();
  private input: AnalyserNode;
  private output: AnalyserNode;
  private inputSource: MediaStreamAudioSourceNode;
  private outputSource: MediaStreamAudioSourceNode | null = null;
  private frame = 0;
  private closed = false;
  private speakingUntil = 0;
  private inputData = new Float32Array(512);
  private outputData = new Float32Array(512);

  constructor(
    stream: MediaStream,
    private update: (input: number, output: number, speaking: boolean) => void
  ) {
    this.input = this.context.createAnalyser();
    this.output = this.context.createAnalyser();
    this.input.fftSize = this.output.fftSize = 512;
    this.inputSource = this.context.createMediaStreamSource(stream);
    this.inputSource.connect(this.input);
    void this.context.resume().catch(() => {});
    this.frame = requestAnimationFrame(this.sample);
  }
  outputStream(stream: MediaStream) {
    this.outputSource?.disconnect();
    this.outputSource = this.context.createMediaStreamSource(stream);
    this.outputSource.connect(this.output);
    void this.context.resume().catch(() => {});
  }
  private sample = (now: number) => {
    if (this.closed) return;
    this.input.getFloatTimeDomainData(this.inputData);
    this.output.getFloatTimeDomainData(this.outputData);
    const input = rmsLevel(this.inputData),
      output = rmsLevel(this.outputData);
    if (output > 0.008) this.speakingUntil = now + 250;
    this.update(visualLevel(input), visualLevel(output), now < this.speakingUntil);
    this.frame = requestAnimationFrame(this.sample);
  };
  close() {
    if (this.closed) return;
    this.closed = true;
    cancelAnimationFrame(this.frame);
    this.inputSource.disconnect();
    this.outputSource?.disconnect();
    this.input.disconnect();
    this.output.disconnect();
    void this.context.close().catch(() => {});
    this.update(0, 0, false);
  }
}
