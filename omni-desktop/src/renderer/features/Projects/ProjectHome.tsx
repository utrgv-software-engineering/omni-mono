import { useStore } from '@nanostores/react';
import { MessageSquare, MessageSquarePlus } from 'lucide-react';
import { memo, useCallback, useMemo } from 'react';

import { Button } from '@/renderer/ds/ui/button';
import { codeApi } from '@/renderer/features/Code/state';
import { persistedStoreApi } from '@/renderer/services/store';
import type { ChatConversation, CodeTab, ProjectId } from '@/shared/types';

const relativeTime = (timestamp: number): string => {
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) {
    return 'Just now';
  }
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  return `${Math.floor(hours / 24)}d ago`;
};

type ChatEntry =
  | { kind: 'open'; key: string; title: string; ts: number; tab: CodeTab }
  | { kind: 'history'; key: string; title: string; ts: number; conversation: ChatConversation };

const openChat = (entry: ChatEntry): void => {
  if (entry.kind === 'open') {
    codeApi.setActiveTab(entry.tab.id);
  } else {
    void codeApi.addTabForConversation(entry.conversation);
  }
  void persistedStoreApi.setKey('layoutMode', 'chat');
};

const ChatRow = memo(({ entry }: { entry: ChatEntry }) => {
  const handleClick = useCallback(() => openChat(entry), [entry]);
  return (
    <Button
      variant="ghost"
      className="h-auto w-full justify-start gap-3 px-3 py-2 text-left font-normal"
      onClick={handleClick}
    >
      <MessageSquare className="text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{entry.title}</span>
      <span className="text-xs text-muted-foreground">{relativeTime(entry.ts)}</span>
    </Button>
  );
});
ChatRow.displayName = 'ChatRow';

/** A project's home: its chats, newest first, and a way to start another. */
export const ProjectHome = memo(({ projectId }: { projectId: ProjectId }) => {
  const store = useStore(persistedStoreApi.$atom);

  const entries = useMemo((): ChatEntry[] => {
    const conversations = (store.chatConversations ?? []).filter(
      (conversation) => conversation.projectId === projectId && !conversation.archivedAt
    );
    const bySession = new Map(conversations.map((conversation) => [conversation.sessionId, conversation]));
    const tabs = (store.codeTabs ?? []).filter((tab) => tab.projectId === projectId);
    const openSessions = new Set(tabs.map((tab) => tab.sessionId).filter(Boolean));
    const open: ChatEntry[] = tabs.map((tab) => {
      const indexed = tab.sessionId ? bySession.get(tab.sessionId) : undefined;
      return {
        kind: 'open',
        key: tab.id,
        title: indexed?.title ?? 'New chat',
        ts: indexed?.lastActiveAt ?? tab.createdAt,
        tab,
      };
    });
    const history: ChatEntry[] = conversations
      .filter((conversation) => !openSessions.has(conversation.sessionId))
      .map((conversation) => ({
        kind: 'history',
        key: conversation.sessionId,
        title: conversation.title,
        ts: conversation.lastActiveAt,
        conversation,
      }));
    return [...open, ...history].sort((a, b) => b.ts - a.ts);
  }, [projectId, store.chatConversations, store.codeTabs]);

  const handleNewChat = useCallback(async () => {
    const tab = await codeApi.addTab();
    await codeApi.setTabProject(tab.id, projectId);
    await persistedStoreApi.setKey('layoutMode', 'chat');
  }, [projectId]);

  return (
    <div className="h-full overflow-y-auto px-6 py-6" data-slot="project-home">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-2 flex items-center gap-2">
          <h2 className="min-w-0 flex-1 text-sm font-medium">Chats</h2>
          <Button size="sm" variant="ghost" onClick={handleNewChat}>
            <MessageSquarePlus />
            New chat
          </Button>
        </div>
        {entries.length ? (
          <div className="space-y-1">
            {entries.map((entry) => (
              <ChatRow key={entry.key} entry={entry} />
            ))}
          </div>
        ) : (
          <p className="rounded-lg bg-muted/40 px-3 py-4 text-sm text-muted-foreground">
            No chats in this project yet.
          </p>
        )}
      </div>
    </div>
  );
});
ProjectHome.displayName = 'ProjectHome';
