import { useEffect, useState } from 'react';
import { useRPCClient } from '../rpc-context';
import { ConversationClient } from '@omniagents/client/rpc/conversation';
import { adaptCanonicalConversationItems } from '@omniagents/client/rpc/canonical-chat-history';
import type { MessageItem } from '@omniagents/client/transcript/chat-types';
import { MessageList } from './MessageList';

/** Read existing history only; never boot, ensure, or change a worker session. */
export function WorkerTranscript({ sessionId }: { sessionId: string }) {
  const client = useRPCClient();
  const [items, setItems] = useState<MessageItem[]>();
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    setItems(undefined);
    setError('');
    void (async () => {
      const conversations = new ConversationClient(client);
      const all = [];
      let cursor: string | undefined;
      do {
        const page = await conversations.listItems(sessionId, { limit: 200, cursor, order: 'asc' });
        all.push(...page.items);
        cursor = page.next_cursor ?? undefined;
        if (disposed) return;
      } while (cursor);
      setItems(adaptCanonicalConversationItems(all));
    })().catch(e => { if (!disposed) setError(e instanceof Error ? e.message : 'Unable to load transcript'); });
    return () => { disposed = true; };
  }, [client, sessionId]);
  if (error) return <p role="alert" className="p-3 text-sm">{error}</p>;
  if (!items) return <p role="status" className="p-3 text-sm text-muted-foreground">Loading transcript…</p>;
  if (!items.length) return <p className="p-3 text-sm text-muted-foreground">No retained transcript.</p>;
  return <MessageList items={items} />;
}
