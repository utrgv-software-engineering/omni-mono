import { describe, expect, it, vi } from 'vitest';
import { createActor } from 'xstate';
import { chatSessionMachine } from '../machines/chat-session.machine';

import { ReplayTracker, type ResumeResult, RESYNC_REQUIRED_CODE, SessionReplayCoordinator } from './replay';

const payload = (seq: number, streamId = 'stream-1') => ({
  session_id: 's',
  seq,
  stream_id: streamId,
});

describe('ReplayTracker', () => {
  it('delivers in-order events and drops duplicates', () => {
    const tracker = new ReplayTracker();
    expect(tracker.observe(payload(1))).toBe('deliver');
    expect(tracker.observe(payload(2))).toBe('deliver');
    expect(tracker.observe(payload(2))).toBe('duplicate');
    expect(tracker.observe(payload(1))).toBe('duplicate');
    expect(tracker.lastSeq).toBe(2);
  });

  it('passes transient events (no seq) through without moving the cursor', () => {
    const tracker = new ReplayTracker();
    tracker.observe(payload(3));
    expect(tracker.observe({ session_id: 's', delta: {} })).toBe('deliver');
    expect(tracker.lastSeq).toBe(3);
  });

  it('detects gaps without advancing the cursor', () => {
    const tracker = new ReplayTracker();
    tracker.observe(payload(1));
    expect(tracker.observe(payload(4))).toBe('gap');
    expect(tracker.lastSeq).toBe(1);
    expect(tracker.gapCount).toBe(1);
  });

  it('adopts the stream mid-flight and flags epoch changes', () => {
    const tracker = new ReplayTracker();
    expect(tracker.observe(payload(41))).toBe('deliver');
    expect(tracker.observe(payload(1, 'stream-2'))).toBe('stream_changed');
    expect(tracker.streamId).toBe('stream-2');
    expect(tracker.observe(payload(2, 'stream-2'))).toBe('deliver');
  });

  it('applyResume skips applied events and adopts last_seq', () => {
    const tracker = new ReplayTracker();
    tracker.observe(payload(1));
    tracker.observe(payload(2));
    const delivered = tracker.applyResume({
      stream_id: 'stream-1',
      last_seq: 6,
      events: [
        { method: 'message_output', params: payload(2) },
        { method: 'tool_called', params: payload(3) },
        // Non-contiguous (compacted snapshot) inside the window is fine.
        { method: 'run_end', params: payload(5) },
      ],
    });
    expect(delivered.map((event) => event.params.seq)).toEqual([3, 5]);
    expect(tracker.lastSeq).toBe(6);
  });
});

describe('SessionReplayCoordinator', () => {
  it('backfills a gap via resume and flushes buffered live events', async () => {
    const delivered: Array<[string, unknown]> = [];
    let resumeCalls = 0;
    const coordinator = new SessionReplayCoordinator(
      async (sessionId, streamId, afterSeq): Promise<ResumeResult> => {
        resumeCalls += 1;
        expect(sessionId).toBe('s');
        expect(streamId).toBe('stream-1');
        expect(afterSeq).toBe(1);
        return {
          session_id: 's',
          stream_id: 'stream-1',
          last_seq: 4,
          events: [
            { method: 'tool_called', params: payload(2) },
            { method: 'tool_result', params: payload(3) },
            { method: 'message_output', params: payload(4) },
          ],
        };
      },
      (method, params) => delivered.push([method, (params as { seq?: number }).seq])
    );

    // seq 1 delivers normally (handle() itself advances the cursor).
    expect(coordinator.handle('run_started', payload(1))).toBe(false);
    // seq 4 arrives next: gap -> handled internally (buffer + resume).
    expect(coordinator.handle('message_output', payload(4))).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resumeCalls).toBe(1);
    expect(delivered).toEqual([
      ['tool_called', 2],
      ['tool_result', 3],
      ['message_output', 4],
    ]);
    // The buffered live event was deduped after the backfill delivered it.
    expect(coordinator.tracker('s').lastSeq).toBe(4);
  });

  it('does not report resync for a transient resume failure', async () => {
    const resyncs: string[] = [];
    const coordinator = new SessionReplayCoordinator(
      async () => {
        throw new Error('code -32030: resync required');
      },
      () => {},
      (sessionId) => resyncs.push(sessionId)
    );
    coordinator.tracker('s').observe(payload(1));
    expect(coordinator.handle('message_output', payload(5))).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resyncs).toEqual([]);
  });

  it('drops duplicate deliveries so no duplicate UI items appear', () => {
    const coordinator = new SessionReplayCoordinator(
      async () => ({}),
      () => {}
    );
    expect(coordinator.handle('message_output', payload(1))).toBe(false);
    expect(coordinator.handle('message_output', payload(1))).toBe(true);
  });

  it('resumeAll backfills tracked cursors and registered cursorless sessions', async () => {
    const delivered: Array<[string, unknown]> = [];
    const resumed: Array<[string, string | null, number]> = [];
    const coordinator = new SessionReplayCoordinator(
      async (sessionId, streamId, afterSeq): Promise<ResumeResult> => {
        resumed.push([sessionId, streamId, afterSeq]);
        return {
          session_id: sessionId,
          stream_id: 'stream-1',
          last_seq: afterSeq + 1,
          events: [{ method: 'run_end', params: { session_id: sessionId, stream_id: 'stream-1', seq: afterSeq + 1 } }],
        };
      },
      (method, params) => delivered.push([method, (params as { seq?: number }).seq])
    );

    coordinator.handle('run_started', { session_id: 'a', stream_id: 'stream-1', seq: 3 });
    coordinator.handle('run_started', { session_id: 'b', stream_id: 'stream-1', seq: 7 });
    // A selected session with no sequenced event must still attach from zero.
    coordinator.registerSession('c');

    await coordinator.resumeAll();

    expect(resumed).toEqual([
      ['a', 'stream-1', 3],
      ['b', 'stream-1', 7],
      ['c', null, 0],
    ]);
    expect(delivered).toEqual([
      ['run_end', 4],
      ['run_end', 8],
      ['run_end', 1],
    ]);
    expect(coordinator.tracker('a').lastSeq).toBe(4);
    expect(coordinator.tracker('b').lastSeq).toBe(8);
    expect(coordinator.tracker('c').lastSeq).toBe(1);
  });

  it('resumeAll buffers live events arriving during the backfill', async () => {
    const delivered: Array<[string, unknown]> = [];
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const coordinator = new SessionReplayCoordinator(
      async (): Promise<ResumeResult> => {
        await gate;
        return {
          session_id: 's',
          stream_id: 'stream-1',
          last_seq: 2,
          events: [{ method: 'tool_result', params: payload(2) }],
        };
      },
      (method, params) => delivered.push([method, (params as { seq?: number }).seq])
    );

    coordinator.handle('run_started', payload(1));
    coordinator.resumeAll();
    // Live event lands while the resume is in flight: held, then flushed.
    expect(coordinator.handle('message_output', payload(3))).toBe(true);
    release!();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(delivered).toEqual([
      ['tool_result', 2],
      ['message_output', 3],
    ]);
    expect(coordinator.tracker('s').lastSeq).toBe(3);
  });

  it('pauses resume after -32030 until authoritative resync completes', async () => {
    const resumed: string[] = [];
    const resyncs: string[] = [];
    const coordinator = new SessionReplayCoordinator(
      async (sessionId): Promise<ResumeResult> => {
        resumed.push(sessionId);
        throw Object.assign(new Error('Resync required'), { code: RESYNC_REQUIRED_CODE });
      },
      () => {},
      (sessionId) => resyncs.push(sessionId)
    );

    coordinator.handle('run_started', payload(3));
    await coordinator.resumeAll();
    expect(resumed).toEqual(['s']);
    expect(resyncs).toEqual(['s']);

    // The unservable cursor is quarantined until the host completes its
    // authoritative rebuild; reconnect must not hammer the same cursor.
    await coordinator.resumeAll();
    expect(resumed).toEqual(['s']);
    expect(resyncs).toEqual(['s']);

    coordinator.completeResync('s');
    coordinator.registerSession('s');
    await coordinator.resumeAll();
    expect(resumed).toEqual(['s', 's']);
  });

  it('keeps the cursor on a transport resume failure and retries on the next reconnect', async () => {
    const resumed: string[] = [];
    const coordinator = new SessionReplayCoordinator(
      async (sessionId): Promise<ResumeResult> => {
        resumed.push(sessionId);
        throw new Error('WebSocket not connected');
      },
      () => {},
      () => {}
    );

    coordinator.handle('run_started', payload(3));
    await coordinator.resumeAll();
    await coordinator.resumeAll();

    expect(resumed).toEqual(['s', 's']);
    expect(coordinator.tracker('s').lastSeq).toBe(3);
  });

  it('unregisters cursorless sessions so reconnect does not recreate them', async () => {
    const resumed: string[] = [];
    const coordinator = new SessionReplayCoordinator(
      async (sessionId) => {
        resumed.push(sessionId);
        return { session_id: sessionId, stream_id: 'stream', last_seq: 0 };
      },
      () => {}
    );

    coordinator.registerSession('selected');
    coordinator.unregisterSession('selected');
    await coordinator.resumeAll();

    expect(resumed).toEqual([]);
  });

  it('ignores an old replay failure after adopting a newer snapshot boundary', async () => {
    let reject!: (reason: unknown) => void;
    const resyncs: string[] = [];
    const coordinator = new SessionReplayCoordinator(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail;
        }),
      () => {},
      (id) => resyncs.push(id)
    );
    coordinator.registerSession('s');
    const pending = coordinator.resumeAll();
    coordinator.beginSnapshot('s');
    coordinator.completeResync('s', 'new-stream', 50);
    reject({ code: RESYNC_REQUIRED_CODE, data: { stream_id: 'old-stream', retained_last_seq: 20 } });
    await pending;
    expect(resyncs).toEqual([]);
    expect(coordinator.tracker('s').streamId).toBe('new-stream');
    expect(coordinator.tracker('s').lastSeq).toBe(50);
  });
});

describe('replay recovery without a new socket or event', () => {
  it('delivers the saved answer and clears Thinking after a transient replay failure', async () => {
    vi.useFakeTimers();
    const actor = createActor(chatSessionMachine).start();
    actor.send({ type: 'SELECT_SESSION', id: 's' });
    actor.send({ type: 'HISTORY_LOADED', items: [] });
    actor.send({ type: 'RUN_STARTED', session_id: 's', run_id: 'r' });
    let attempts = 0;
    const coordinator = new SessionReplayCoordinator(
      async () => {
        if (++attempts === 1) throw new Error('Replay request timed out');
        return {
          stream_id: 'stream-1',
          last_seq: 3,
          events: [
            { method: 'message_output', params: { ...payload(2), content: 'Saved answer', run_id: 'r' } },
            { method: 'run_end', params: { ...payload(3), run_id: 'r' } },
          ],
        };
      },
      (method, params) => {
        if (method === 'message_output')
          actor.send({ type: 'MESSAGE_OUTPUT', session_id: 's', content: String(params.content) });
        if (method === 'run_end') actor.send({ type: 'RUN_END', session_id: 's', run_id: 'r' });
      }
    );
    try {
      coordinator.handle('run_started', payload(1));
      coordinator.handle('run_end', payload(3));
      await vi.advanceTimersByTimeAsync(0);
      expect(actor.getSnapshot().matches({ ready: 'running' })).toBe(true);
      expect(actor.getSnapshot().context.items).toHaveLength(0);
      await vi.advanceTimersByTimeAsync(1000);
      expect(attempts).toBe(2);
      expect(actor.getSnapshot().matches({ ready: 'idle' })).toBe(true);
      expect(actor.getSnapshot().context.items).toHaveLength(1);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      coordinator.dispose();
      actor.stop();
      vi.useRealTimers();
    }
  });

  it('falls back to an authoritative reload after three retries', async () => {
    vi.useFakeTimers();
    const resume = vi.fn(async () => {
      throw new Error('Replay unavailable');
    });
    const reload = vi.fn();
    const coordinator = new SessionReplayCoordinator(resume, () => {}, reload);
    try {
      coordinator.handle('run_started', payload(1));
      coordinator.handle('run_end', payload(3));
      await vi.advanceTimersByTimeAsync(7000);
      expect(resume).toHaveBeenCalledTimes(4);
      expect(reload).toHaveBeenCalledExactlyOnceWith('s');
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      coordinator.dispose();
      vi.useRealTimers();
    }
  });

  it.each(['snapshot', 'unregister', 'dispose'] as const)('cancels pending retries on %s', async (operation) => {
    vi.useFakeTimers();
    const resume = vi.fn(async () => {
      throw new Error('Transient failure');
    });
    const coordinator = new SessionReplayCoordinator(resume, () => {});
    try {
      coordinator.handle('run_started', payload(1));
      coordinator.handle('run_end', payload(3));
      await vi.advanceTimersByTimeAsync(0);
      if (operation === 'snapshot') coordinator.beginSnapshot('s');
      if (operation === 'unregister') coordinator.unregisterSession('s');
      if (operation === 'dispose') coordinator.dispose();
      await vi.advanceTimersByTimeAsync(10000);
      expect(resume).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      coordinator.dispose();
      vi.useRealTimers();
    }
  });
});
