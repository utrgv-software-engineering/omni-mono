import { afterEach, expect, it, vi } from 'vitest';
vi.mock('./voice-audio-meter', () => ({ VoiceAudioMeter: vi.fn(() => ({ outputStream: vi.fn(), close: vi.fn() })) }));
import { CodexLiveTransport, liveChunks } from './codex-live';

const settings = {
  inputDeviceId: null,
  outputDeviceId: null,
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
it('bounds append chunks by UTF-8 bytes without losing text', () => {
  const text = 'hé🙂'.repeat(300),
    chunks = liveChunks(text);
  expect(chunks.join('')).toBe(text);
  expect(chunks.every((c) => new TextEncoder().encode(c).length <= 500)).toBe(true);
});
it('delegates once, flushes transcripts and closes media without stopping agent work', async () => {
  vi.useFakeTimers();
  const track = { stop: vi.fn(), enabled: true };
  vi.stubGlobal('navigator', {
    mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track], getAudioTracks: () => [track] })) },
  });
  const dc = { readyState: 'open', send: vi.fn(), onmessage: null as any, onclose: null };
  const pc = {
    createDataChannel: () => dc,
    addTrack: vi.fn(),
    createOffer: async () => ({ sdp: 'offer' }),
    setLocalDescription: vi.fn(),
    setRemoteDescription: vi.fn(),
    close: vi.fn(),
  };
  vi.stubGlobal(
    'RTCPeerConnection',
    vi.fn(() => pc)
  );
  vi.stubGlobal(
    'Audio',
    vi.fn(() => ({ pause: vi.fn(), play: vi.fn() }))
  );
  const client = {
    on: vi.fn(() => vi.fn()),
    startLive: vi.fn(async () => ({ sdp: 'answer' })),
    stopSession: vi.fn(),
    delegate: vi.fn(async () => ({ accepted: true })),
    syncContext: vi.fn(async (_sid, turns) => ({ saved: turns.map((t: any) => t.id), context: '' })),
  };
  const live = new CodexLiveTransport(client as any, 'shared', vi.fn(), vi.fn(), vi.fn());
  await live.open(settings);
  const message = (m: unknown) => dc.onmessage({ data: JSON.stringify(m) });
  message({ type: 'input_transcript.added', item: { text: 'hello' } });
  const delegation = {
    type: 'delegation.created',
    item: { id: 'd1', target: 'client', content: [{ type: 'input_text', text: 'work' }] },
  };
  message(delegation);
  message(delegation);
  expect(client.delegate).toHaveBeenCalledTimes(1);
  live.mute(true);
  expect(track.enabled).toBe(false);
  await live.close();
  await live.close();
  expect(client.syncContext).toHaveBeenCalledWith('shared', [{ id: '1', role: 'user', text: 'hello' }]);
  expect(track.stop).toHaveBeenCalledOnce();
  expect(pc.close).toHaveBeenCalledOnce();
  expect(client.stopSession).toHaveBeenCalledExactlyOnceWith('shared');
});
it('stops a microphone granted after the call was closed', async () => {
  const track = { stop: vi.fn() };
  let grant!: (stream: any) => void;
  vi.stubGlobal('navigator', {
    mediaDevices: {
      getUserMedia: () =>
        new Promise((resolve) => {
          grant = resolve;
        }),
    },
  });
  const client = { stopSession: vi.fn() };
  const live = new CodexLiveTransport(client as any, 'shared', vi.fn(), vi.fn(), vi.fn());
  const opening = live.open(settings);
  await live.close();
  grant({ getTracks: () => [track] });
  await opening;
  expect(track.stop).toHaveBeenCalledOnce();
  expect(client.stopSession).not.toHaveBeenCalled();
});
