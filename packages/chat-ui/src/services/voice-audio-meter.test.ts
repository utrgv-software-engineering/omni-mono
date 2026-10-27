import { afterEach, expect, it, vi } from 'vitest';
import { VoiceAudioMeter, rmsLevel } from './voice-audio-meter';
afterEach(() => vi.unstubAllGlobals());
it('measures waveform energy, including opposite signs and silence', () => {
  expect(rmsLevel(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5);
  expect(rmsLevel(new Float32Array(512))).toBe(0);
});
it('observes separate tracks, detects output speech and releases resources', () => {
  let frame!: (now: number) => void;
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn((callback) => {
      frame = callback;
      return 1;
    })
  );
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  let mic = 0.1,
    speaker = 0;
  const input = { disconnect: vi.fn(), getFloatTimeDomainData: (data: Float32Array) => data.fill(mic) };
  const output = { disconnect: vi.fn(), getFloatTimeDomainData: (data: Float32Array) => data.fill(speaker) };
  const sources = [
    { connect: vi.fn(), disconnect: vi.fn() },
    { connect: vi.fn(), disconnect: vi.fn() },
  ];
  const context = {
    createAnalyser: vi.fn().mockReturnValueOnce(input).mockReturnValueOnce(output),
    createMediaStreamSource: vi.fn().mockReturnValueOnce(sources[0]).mockReturnValueOnce(sources[1]),
    resume: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
  };
  vi.stubGlobal(
    'AudioContext',
    vi.fn(() => context)
  );
  const update = vi.fn(),
    meter = new VoiceAudioMeter({} as MediaStream, update);
  meter.outputStream({} as MediaStream);
  frame(1000);
  expect(update).toHaveBeenLastCalledWith(expect.any(Number), 0, false);
  mic = 0;
  speaker = 0.2;
  frame(1100);
  expect(update).toHaveBeenLastCalledWith(0, 1, true);
  speaker = 0;
  frame(1200);
  expect(update).toHaveBeenLastCalledWith(0, 0, true);
  frame(1400);
  expect(update).toHaveBeenLastCalledWith(0, 0, false);
  // Only analysers receive the tracks: the meter never duplicates speaker playback.
  expect(sources[0].connect).toHaveBeenCalledExactlyOnceWith(input);
  expect(sources[1].connect).toHaveBeenCalledExactlyOnceWith(output);
  meter.close();
  meter.close();
  expect(context.close).toHaveBeenCalledOnce();
  expect(sources[0].disconnect).toHaveBeenCalledOnce();
  expect(sources[1].disconnect).toHaveBeenCalledOnce();
  expect(update).toHaveBeenLastCalledWith(0, 0, false);
});
