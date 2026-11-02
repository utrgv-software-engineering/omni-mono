import type { ChatMessage } from './chat-types';

export type WorkerMessage = { id: string; session_id?: string; name: string; task?: string; message: string; message_id?: string };
export type Notification = { source: string; content: string; worker?: WorkerMessage };

export function parseNotifications(value: unknown): Notification[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: Notification[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== 'object' || typeof entry.source !== 'string' || typeof entry.content !== 'string') continue;
    const w = entry.worker;
    const worker = w && typeof w.id === 'string' && typeof w.message === 'string'
      ? { id: w.id, session_id: typeof w.session_id === 'string' ? w.session_id : undefined, name: typeof w.name === 'string' ? w.name : w.id,
          task: typeof w.task === 'string' ? w.task : undefined, message: w.message,
          message_id: typeof w.message_id === 'string' ? w.message_id : undefined } : undefined;
    out.push({ source: entry.source, content: entry.content, worker });
  }
  return out.length ? out : undefined;
}

export type NotificationPart = { content: string; worker?: WorkerMessage };

/** Compatibility for saved prompts from before structured notification metadata. */
function legacy(content: string): NotificationPart[] {
  const header = /^(?:  - )?\[worker message\] ([\w.-]+):[ \t]?/gm;
  const matches = [...content.matchAll(header)];
  if (!matches.length || !/^(?:\[worker\.message\] \d+ events:\n)?(?:  - )?\[worker message\] /.test(content)) return [{ content }];
  return matches.map((match, i) => {
    const message = content.slice(match.index! + match[0].length, matches[i + 1]?.index ?? content.length).trimEnd();
    return { content: message, worker: { id: match[1]!, name: match[1]!, message } };
  });
}

export function workerMessageParts(message: ChatMessage): NotificationPart[] | undefined {
  if (message.role !== 'assistant') return undefined;
  const notices = message.notifications;
  const parts = notices
    ? notices.flatMap(n => n.source === 'worker.message'
      ? n.worker ? [{ content: n.worker.message, worker: n.worker }] : legacy(n.content)
      : [{ content: n.content }])
    : legacy(message.content);
  return parts.some(p => p.worker) ? parts : undefined;
}
