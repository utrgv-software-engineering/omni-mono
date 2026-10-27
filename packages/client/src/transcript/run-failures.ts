import type { ConversationTurn } from '../rpc/conversation';
import type { ChatMessage, MessageItem } from './chat-types';

/**
 * A run that fails before producing anything (a rejected key, an unreachable
 * provider) would otherwise end in silence: the transcript says so instead.
 */
export function runFailureNotice(error: unknown): ChatMessage {
  const message = (error as { message?: unknown } | null)?.message;
  const detail = typeof message === 'string' ? message.trim() : '';
  return { type: 'chat', role: 'system', content: detail ? `The run failed: ${detail}` : 'The run failed.' };
}

/** Place a failure notice after the last item of each turn that ended in error. */
export function withRunFailureNotices(items: MessageItem[], turns: readonly ConversationTurn[]): MessageItem[] {
  const failed = new Map(turns.filter((turn) => turn.end_reason === 'error').map((turn) => [turn.turn_id, turn]));
  if (failed.size === 0) {
    return items;
  }
  const lastIndexOfTurn = new Map<string, number>();
  items.forEach((item, index) => {
    const turnId = 'canonical' in item ? item.canonical?.turn_id : undefined;
    if (turnId && failed.has(turnId)) {
      lastIndexOfTurn.set(turnId, index);
    }
  });
  const noticesAfter = new Map<number, MessageItem[]>();
  for (const [turnId, index] of lastIndexOfTurn) {
    noticesAfter.set(index, [...(noticesAfter.get(index) ?? []), runFailureNotice(failed.get(turnId)!.error)]);
  }
  return items.flatMap((item, index) => [item, ...(noticesAfter.get(index) ?? [])]);
}
