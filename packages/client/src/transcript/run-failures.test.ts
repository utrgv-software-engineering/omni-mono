import { expect, it } from 'vitest';

import type { ConversationTurn } from '../rpc/conversation';
import type { MessageItem } from './chat-types';
import { runFailureNotice, withRunFailureNotices } from './run-failures';

const item = (turnId: string, content: string): MessageItem => ({
  type: 'chat',
  role: 'user',
  content,
  canonical: {
    item_id: `item-${content}`,
    thread_id: 't',
    turn_id: turnId,
    seq: 1,
    kind: 'user_message',
    status: 'completed',
    revision: 1,
    created_at: 1,
    updated_at: 1,
    content: {},
    source_ref: {},
  },
});

const turn = (turnId: string, endReason: string, error: Record<string, unknown> | null = null) =>
  ({ turn_id: turnId, end_reason: endReason, error }) as ConversationTurn;

it('puts each failure notice after its turn', () => {
  const items = [item('a', 'first'), item('b', 'second'), item('b', 'second, more'), item('c', 'third')];
  const result = withRunFailureNotices(items, [
    turn('a', 'completed'),
    turn('b', 'error', { message: 'Error code: 401' }),
    turn('c', 'error'),
  ]);
  expect(result.map((entry) => (entry.type === 'chat' ? entry.content : entry.type))).toEqual([
    'first',
    'second',
    'second, more',
    'The run failed: Error code: 401',
    'third',
    'The run failed.',
  ]);
});

it('leaves a transcript without failed turns untouched', () => {
  const items = [item('a', 'first')];
  expect(withRunFailureNotices(items, [turn('a', 'completed')])).toBe(items);
});

it('words the notice from the runtime error', () => {
  expect(runFailureNotice({ message: '  boom  ' }).content).toBe('The run failed: boom');
  expect(runFailureNotice(null).content).toBe('The run failed.');
});
