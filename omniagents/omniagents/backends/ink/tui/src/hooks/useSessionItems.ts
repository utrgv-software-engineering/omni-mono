/**
 * Another conversation's transcript, read live through the shared session
 * registry: a worker owns a session, so its activity is that session's real
 * thread, exactly as the desktop shows it.
 */

import { useEffect, useState } from "react";
import type { RPCClient } from "@omniagents/client/rpc/client";
import { getSessionRegistry } from "@omniagents/client/session/session-registry";
import type { MessageItem } from "@omniagents/client/transcript/chat-types";

const NONE: readonly MessageItem[] = [];

export function useSessionItems(
  client: RPCClient | null,
  sessionId: string | null,
): readonly MessageItem[] {
  const [items, setItems] = useState<readonly MessageItem[]>(NONE);
  useEffect(() => {
    setItems(NONE);
    if (!client || !sessionId) return;
    const registry = getSessionRegistry(client);
    const session = registry.get(sessionId);
    const release = registry.retainSession(sessionId);
    const subscription = session.actor.subscribe((snapshot) =>
      setItems(snapshot.context.items as MessageItem[]),
    );
    setItems(session.actor.getSnapshot().context.items as MessageItem[]);
    // A transcript that cannot be read leaves the detail with the worker's
    // summary; the screen stays usable.
    session.load().catch(() => {});
    return () => {
      subscription.unsubscribe();
      release();
    };
  }, [client, sessionId]);
  return items;
}
