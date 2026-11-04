import { afterEach, expect, it } from 'vitest';

import { ConversationSession, HISTORY_PAGE } from './conversation-session';
import { deferred, fakeSessionClient } from './session-test-support';

const sessions: ConversationSession[] = [];
afterEach(() => sessions.splice(0).forEach((session) => session.dispose()));

function item(id: string, seq: number) {
  return {
    item_id: `item-${seq}`,
    thread_id: id,
    turn_id: null,
    seq,
    kind: 'user_message',
    status: 'completed',
    role: 'user',
    created_at: seq,
    updated_at: seq,
    completed_at: seq,
    revision: 0,
    content: { text: `message ${seq}` },
    source_ref: {},
    long_lived: false,
    source: 'recorded',
    schema_version: 1,
  };
}

function setup(id: string, page: (params: any) => Promise<any>) {
  const { fake, client } = fakeSessionClient();
  fake.getSessionSnapshot.mockImplementation(async (sessionId: string) => ({
    session_id: sessionId,
    run_active: false,
    snapshot: {
      stream_id: 'stream',
      last_seq: 0,
      items: [item(sessionId, 3), item(sessionId, 4)],
      queue: [],
      pending_requests: [],
      state_events: [],
      earlier_cursor: 'before-3',
    },
  }));
  fake.request.mockImplementation(async (method: string, params: any) => {
    if (method === 'list_items' && params.cursor) {
      return page(params);
    }
    throw new Error(`Unexpected request: ${method}`);
  });
  const session = new ConversationSession(id, client);
  sessions.push(session);
  return { fake, session };
}

const seqs = (session: ConversationSession) =>
  session.actor.getSnapshot().context.items.map((entry) => entry.canonical?.seq);

it('opens on the newest items and reads earlier ones on request, first', async () => {
  const pages: any[] = [];
  const { fake, session } = setup('long', async (params) => {
    pages.push(params);
    return {
      thread_id: 'long',
      turn_id: null,
      // Newest first, as an order: "desc" page reads.
      items: [item('long', 3), item('long', 2), item('long', 1)],
      has_more: false,
      next_cursor: null,
      total: 4,
    };
  });
  await session.load();
  expect(fake.getSessionSnapshot).toHaveBeenCalledWith('long', { limit: HISTORY_PAGE });
  expect(seqs(session)).toEqual([3, 4]);
  expect(session.panels.state.get().earlierHistory).toBe('more');

  await session.loadEarlier();
  expect(pages).toEqual([{ thread_id: 'long', limit: HISTORY_PAGE, cursor: 'before-3', order: 'desc' }]);
  // Seq 3 was already shown; it is not repeated.
  expect(seqs(session)).toEqual([1, 2, 3, 4]);
  expect(session.panels.state.get().earlierHistory).toBe('none');
  await session.loadEarlier();
  expect(pages).toHaveLength(1);
});

it('discards a page read for a transcript that a reload has replaced', async () => {
  const reply = deferred<any>();
  const { session } = setup('reloaded', () => reply.promise);
  await session.load();
  const reading = session.loadEarlier();
  expect(session.panels.state.get().earlierHistory).toBe('loading');
  await session.load({ force: true });
  reply.resolve({
    thread_id: 'reloaded',
    turn_id: null,
    items: [item('reloaded', 2)],
    has_more: false,
    next_cursor: null,
    total: 3,
  });
  await reading;
  expect(seqs(session)).toEqual([3, 4]);
  expect(session.panels.state.get().earlierHistory).toBe('more');
});
