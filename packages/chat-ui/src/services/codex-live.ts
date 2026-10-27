import { VoiceAudioMeter } from './voice-audio-meter';
import type { RealtimeRPCClient } from '@omniagents/client/rpc/realtime';
import type { AudioSettings } from '../host';

type Turn = { id: string; role: 'user' | 'assistant'; text: string; closed: boolean };

/** Provider's append bound is bytes, not JavaScript characters. */
export function liveChunks(text: string): string[] {
  const result: string[] = [];
  let chunk = '',
    size = 0;
  const encoder = new TextEncoder();
  for (const char of text) {
    const length = encoder.encode(char).length;
    if (size + length > 500) {
      result.push(chunk);
      chunk = '';
      size = 0;
    }
    chunk += char;
    size += length;
  }
  if (chunk) result.push(chunk);
  return result;
}

/** WebRTC transport behind the shared voice UI. Owns no agent execution. */
export class CodexLiveTransport {
  private pc: RTCPeerConnection | null = null;
  private meter: VoiceAudioMeter | null = null;
  private dc: RTCDataChannel | null = null;
  private stream: MediaStream | null = null;
  private output: HTMLAudioElement | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private started = false;
  private ready = false;
  private turns: Turn[] = [];
  private serial = 0;
  private callId: string = crypto.randomUUID();
  private saved = new Set<string>();
  private delegated = new Set<string>();
  private syncing: Promise<void> | null = null;
  private off: (() => void) | null = null;
  private offClose: (() => void) | null = null;
  private hidden = () => {
    if (document.hidden) this.end();
  };

  constructor(
    private client: RealtimeRPCClient,
    private sessionId: string,
    private event: (payload: Record<string, unknown>) => void,
    private end: () => void,
    private fail: (message: string) => void,
    private levels: (input: number, output: number, speaking: boolean) => void = () => {}
  ) {}

  async open(settings: AudioSettings): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: settings.inputDeviceId ? { exact: settings.inputDeviceId } : undefined,
        echoCancellation: settings.echoCancellation,
        noiseSuppression: settings.noiseSuppression,
        autoGainControl: settings.autoGainControl,
      },
    });
    if (this.closed) {
      this.stream.getTracks().forEach((t) => t.stop());
      return;
    }
    this.meter = new VoiceAudioMeter(this.stream, this.levels);
    const pc = (this.pc = new RTCPeerConnection());
    this.output = new Audio();
    this.output.autoplay = true;
    if (settings.outputDeviceId && this.output.setSinkId) await this.output.setSinkId(settings.outputDeviceId);
    if (this.closed) return;
    pc.ontrack = (e) => {
      if (!this.closed && this.output) {
        const stream = new MediaStream([e.track]);
        this.output.srcObject = stream;
        this.meter?.outputStream(stream);
        void this.output.play().catch((e) => this.fail(String(e)));
      }
    };
    pc.onconnectionstatechange = () => {
      if (!this.closed && ['failed', 'disconnected', 'closed'].includes(pc.connectionState))
        this.fail('Voice connection ended. Reconnect to continue.');
    };
    this.stream.getAudioTracks().forEach((track) => pc.addTrack(track, this.stream!));
    const dc = (this.dc = pc.createDataChannel('oai-events'));
    dc.onmessage = (e) => this.message(e.data);
    dc.onclose = () => {
      if (!this.closed) this.end();
    };
    this.offClose = this.client.on('transport_closed', () => {
      if (!this.closed) this.fail('Voice connection closed. Reconnect to continue.');
    });
    this.off = this.client.on('realtime_event', (e) => {
      if (e?.session_id !== this.sessionId) return;
      if (['live_delegation_result', 'live_delegation_output', 'live_delegation_progress'].includes(String(e.type))) {
        this.append(
          'delegation.context.append',
          String(e.text ?? '').slice(0, 12000),
          e.type === 'live_delegation_progress' ? 'commentary' : 'speakable',
          String(e.delegation_id)
        );
      } else if (e.type === 'realtime_error') this.fail(String(e.error_message || 'Voice disconnected'));
    });
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const answer = await this.client.startLive(this.sessionId, offer.sdp!);
    this.callId = answer.call_id ?? this.callId;
    this.started = true;
    if (this.closed) {
      await this.client.stopSession(this.sessionId);
      return;
    }
    await pc.setRemoteDescription({ type: 'answer', sdp: answer.sdp });
    if (!this.ready) this.deadline = setTimeout(() => this.fail('Voice audio connection timed out'), 20000);
    this.timer = setInterval(() => {
      void this.sync().catch((e) => this.fail(String(e)));
    }, 2000);
    document.addEventListener('visibilitychange', this.hidden);
  }

  private send(event: Record<string, unknown>) {
    if (!this.closed && this.dc?.readyState === 'open') this.dc.send(JSON.stringify(event));
  }
  private append(type: string, text: string, channel: string, id?: string) {
    for (const chunk of liveChunks(text))
      this.send({
        type,
        channel,
        ...(id ? { delegation_item_id: id } : {}),
        content: [{ type: 'input_text', text: chunk }],
      });
  }
  private message(raw: string) {
    if (this.closed) return;
    let m: any;
    try {
      m = JSON.parse(raw);
    } catch {
      return;
    }
    if (m.type === 'session.started') {
      this.ready = true;
      if (this.deadline) clearTimeout(this.deadline);
    } else if (m.type === 'input_transcript.added' || m.type === 'output_transcript.added') {
      const role = m.type === 'input_transcript.added' ? 'user' : 'assistant';
      let row = this.turns.at(-1);
      if (!row || row.role !== role || row.closed) {
        if (row) row.closed = true;
        row = { id: String(++this.serial), role, text: '', closed: false };
        this.turns.push(row);
      }
      row.text = (row.text + String(m.item?.text || '')).slice(0, 12000);
      this.event({ type: 'realtime_transcript', item_id: `live:${this.callId}:${row.id}`, role, transcript: row.text });
    } else if (m.type === 'turn.done') {
      const row = [...this.turns].reverse().find((t) => t.role === m.turn?.role);
      if (row) row.closed = true;
    } else if (m.type === 'delegation.created' && m.item?.target === 'client') {
      const id = String(m.item.id || '');
      if (!id || this.delegated.has(id)) return;
      this.delegated.add(id);
      const text = (m.item.content || [])
        .filter((c: any) => c.type === 'input_text')
        .map((c: any) => c.text || '')
        .join('');
      void this.client.delegate(this.sessionId, id, text).catch((e) => {
        this.append('delegation.context.append', `Request not accepted: ${e.message}`, 'speakable', id);
      });
    } else if (m.type === 'session.closed') this.end();
    else if (m.type === 'error' && !/cancel|interrupt/i.test(m.error?.message || m.error?.code || ''))
      this.fail(m.error?.message || 'Live voice error');
  }

  private async sync(final = false): Promise<void> {
    if (this.syncing) {
      await this.syncing;
      if (!final) return;
    }
    if (!this.started) return;
    const work = async () => {
      const turns = this.turns.filter((t) => (final || t.closed) && !this.saved.has(t.id)).slice(0, 80);
      const response = await this.client.syncContext(
        this.sessionId,
        turns.map(({ id, role, text }) => ({ id, role, text }))
      );
      response.saved.forEach((id) => this.saved.add(id));
      this.turns = this.turns.filter((t) => !this.saved.has(t.id));
      if (!final && response.context) this.append('session.context.append', response.context, 'commentary');
    };
    this.syncing = work();
    try {
      await this.syncing;
    } finally {
      this.syncing = null;
    }
  }
  mute(value: boolean) {
    this.stream?.getAudioTracks().forEach((t) => {
      t.enabled = !value;
    });
  }
  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this.timer) clearInterval(this.timer);
    if (this.deadline) clearTimeout(this.deadline);
    document.removeEventListener('visibilitychange', this.hidden);
    this.meter?.close();
    this.meter = null;
    this.off?.();
    this.offClose?.();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.pc?.close();
    this.output?.pause();
    if (this.output) this.output.srcObject = null;
    try {
      await this.sync(true);
    } finally {
      if (this.started) await this.client.stopSession(this.sessionId);
    }
  }
}
