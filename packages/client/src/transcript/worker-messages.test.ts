import { describe, expect, it } from 'vitest';
import { parseNotifications, workerMessageParts } from './worker-messages';
import { adaptCanonicalConversationItem } from '../rpc/canonical-chat-history';
import type { ChatMessage } from './chat-types';
import type { ConversationItem } from '../rpc/conversation';

describe('worker handoffs', () => {
  const content = '[worker message] nyx-123: Full report';
  const notices = [{ source: 'worker.message', content,
    worker: { id: 'nyx-123', session_id: 'child', name: 'nyx', task: 'Design concept C', message: 'Full report', message_id: 'mail-1' } }];
  it('preserves the same structured report in live and canonical history, including mixed notifications', () => {
    const notifications = [...notices, { source: 'bash', content: 'Build failed' }];
    const live: ChatMessage = { type: 'chat', role: 'assistant', content, notifications: parseNotifications(notifications) };
    const history = adaptCanonicalConversationItem({
      kind: 'agent_message', role: 'assistant', content: { text: content, notifications },
      source_ref: { event: 'run_started' },
    } as unknown as ConversationItem) as ChatMessage;
    expect(workerMessageParts(history)).toEqual(workerMessageParts(live));
    expect(workerMessageParts(history)).toEqual([
      { content: 'Full report', worker: notices[0]!.worker }, { content: 'Build failed' },
    ]);
  });
  it('collapses older single messages and batches without losing report text', () => {
    const parts = workerMessageParts({ type: 'chat', role: 'assistant',
      content: '[worker.message] 2 events:\n  - [worker message] one: first\nsecond line\n  - [worker message] two: last' });
    expect(parts?.map(p => p.content)).toEqual(['first\nsecond line', 'last']);
  });
  it('does not reclassify user messages or ordinary assistant prose', () => {
    expect(workerMessageParts({ type: 'chat', role: 'user', content })).toBeUndefined();
    expect(workerMessageParts({ type: 'chat', role: 'assistant', content: 'Example:\n' + content })).toBeUndefined();
    expect(parseNotifications([null, { source: 'worker.message' }])).toBeUndefined();
  });
});
