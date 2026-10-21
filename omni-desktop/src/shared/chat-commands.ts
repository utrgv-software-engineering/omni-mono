import { nanoid } from 'nanoid';

import { pruneConversations, upsertConversation } from '@/lib/chat-conversations';
import { uuidv4 } from '@/lib/uuid';
import type { ChatConversation, CodeTab, CodeTabId, ProjectId, StoreData } from '@/shared/types';
import { isChatColumn } from '@/shared/types';

type ChatState = Pick<StoreData, 'codeTabs' | 'chatConversations' | 'activeCodeTabId'>;

/** Synchronous, side-effect-free command reducer. Only the authority commits its patch. */
function operations(initial: StoreData) {
  let state = initial;
  const patch: Partial<ChatState> = {};
  const persistedStoreApi = {
    getKey: <K extends keyof StoreData>(key: K): StoreData[K] => state[key],
    setKey: <K extends keyof ChatState>(key: K, value: ChatState[K]): void => {
      state = { ...state, [key]: value };
      Object.assign(patch, { [key]: value });
    },
  };
  const codeApi = {
    ensureRoutineTab: (candidate: CodeTab, activate: boolean): CodeTab => {
      const tabs = persistedStoreApi.getKey('codeTabs') ?? [];
      const existing = tabs.find((tab) => tab.sessionId === candidate.sessionId);
      const result = existing
        ? {
            ...existing,
            routineId: candidate.routineId,
            routineName: candidate.routineName,
            routineSchedule: candidate.routineSchedule,
          }
        : // A stale window can pass the former tab after another window closed
          // it. Reopening must mint a new consumer identity.
          { ...candidate, id: nanoid() };
      persistedStoreApi.setKey(
        'codeTabs',
        existing ? tabs.map((tab) => (tab.id === existing.id ? result : tab)) : [...tabs, result]
      );
      if (activate) {
        codeApi.setActiveTab(result.id);
      }
      return result;
    },
    openFreshChat: (): void => {
      const tabs = persistedStoreApi.getKey('codeTabs') ?? [];
      const known = new Set((persistedStoreApi.getKey('chatConversations') ?? []).map((c) => c.sessionId));
      const pristine = tabs.find((t) => !t.projectId && !t.routineId && (!t.sessionId || !known.has(t.sessionId)));
      if (pristine) {
        persistedStoreApi.setKey('activeCodeTabId', pristine.id);
        return;
      }
      codeApi.addTab();
    },
    addTab: (): CodeTab => {
      const existingTabs = persistedStoreApi.getKey('codeTabs') ?? [];
      const tab: CodeTab = {
        id: nanoid(),
        projectId: null,
        sessionId: uuidv4(),
        createdAt: Date.now(),
      };
      const tabs = [...existingTabs, tab];
      persistedStoreApi.setKey('codeTabs', tabs);
      persistedStoreApi.setKey('activeCodeTabId', tab.id);
      return tab;
    },
    removeTab: (tabId: CodeTabId): CodeTab | undefined => {
      const all = persistedStoreApi.getKey('codeTabs') ?? [];
      const tab = all.find((t) => t.id === tabId);
      const tabs = all.filter((t) => t.id !== tabId);
      persistedStoreApi.setKey('codeTabs', tabs);
      if (persistedStoreApi.getKey('activeCodeTabId') === tabId) {
        persistedStoreApi.setKey('activeCodeTabId', tabs.at(-1)?.id ?? null);
      }
      return tab;
    },
    setActiveTab: (tabId: CodeTabId) => {
      if (tabId === '__launcher__' || (persistedStoreApi.getKey('codeTabs') ?? []).some((tab) => tab.id === tabId)) {
        persistedStoreApi.setKey('activeCodeTabId', tabId);
      }
    },
    setTabProject: (tabId: CodeTabId, projectId: ProjectId) => {
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((t) => {
        if (t.id !== tabId) {
          return t;
        }
        // Attaching a project is intent — it activates a lazy chat column.
        return { ...t, projectId, activatedAt: t.activatedAt ?? Date.now() };
      });
      persistedStoreApi.setKey('codeTabs', tabs);
      const attached = tabs.find((tab) => tab.id === tabId);
      if (
        attached?.sessionId &&
        (persistedStoreApi.getKey('chatConversations') ?? []).some(
          (conversation) => conversation.sessionId === attached.sessionId
        )
      ) {
        codeApi.recordConversation(attached.sessionId, { projectId });
      }
    },
    setTabActivated: (tabId: CodeTabId) => {
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((t) =>
        t.id === tabId && !t.activatedAt ? { ...t, activatedAt: Date.now() } : t
      );
      persistedStoreApi.setKey('codeTabs', tabs);
    },
    addTabForConversation: (conversation: ChatConversation): CodeTab => {
      // Another window may have changed the title or workspace since the
      // caller rendered its history list. Reopen from authoritative metadata.
      conversation =
        (persistedStoreApi.getKey('chatConversations') ?? []).find((c) => c.sessionId === conversation.sessionId) ??
        conversation;
      const existingTabs = persistedStoreApi.getKey('codeTabs') ?? [];
      const existing = existingTabs.find((t) => t.sessionId === conversation.sessionId);
      if (existing) {
        persistedStoreApi.setKey('activeCodeTabId', existing.id);
        return existing;
      }
      const tab: CodeTab = {
        id: nanoid(),
        projectId: conversation.projectId ?? null,
        sessionId: conversation.sessionId,
        createdAt: Date.now(),
        activatedAt: Date.now(),
      };
      persistedStoreApi.setKey('codeTabs', [...existingTabs, tab]);
      persistedStoreApi.setKey('activeCodeTabId', tab.id);
      // Persist the entry (notably the title) in the launcher index: a
      // conversation surfaced only by the live session listing would otherwise
      // lose its title whenever no chat column is running to list it.
      codeApi.recordConversation(conversation.sessionId, { title: conversation.title });
      return tab;
    },
    recordConversation: (sessionId: string, patch?: Partial<ChatConversation>) => {
      const list = persistedStoreApi.getKey('chatConversations') ?? [];
      const tab = (persistedStoreApi.getKey('codeTabs') ?? []).find((candidate) => candidate.sessionId === sessionId);
      const inferredContext: Partial<ChatConversation> = tab
        ? {
            ...(tab.projectId ? { projectId: tab.projectId } : {}),
          }
        : {};
      const { kept } = pruneConversations(
        upsertConversation(list, { sessionId, lastActiveAt: Date.now(), ...inferredContext, ...patch })
      );
      persistedStoreApi.setKey('chatConversations', kept);
    },
    archiveConversation: (conversation: ChatConversation) => {
      const list = persistedStoreApi.getKey('chatConversations') ?? [];
      const { kept } = pruneConversations(
        upsertConversation(list, {
          ...(list.find((item) => item.sessionId === conversation.sessionId) ?? conversation),
          archivedAt: Date.now(),
        })
      );
      persistedStoreApi.setKey('chatConversations', kept);
    },
    archiveTab: (tabId: CodeTabId, title?: string) => {
      const tab = (persistedStoreApi.getKey('codeTabs') ?? []).find((candidate) => candidate.id === tabId);
      if (tab?.sessionId) {
        const indexed = (persistedStoreApi.getKey('chatConversations') ?? []).find(
          (candidate) => candidate.sessionId === tab.sessionId
        );
        codeApi.recordConversation(tab.sessionId, {
          title: title ?? indexed?.title ?? tab.routineName ?? 'New chat',
          ...(tab.projectId ? { projectId: tab.projectId } : {}),
        });
        const conversation = (persistedStoreApi.getKey('chatConversations') ?? []).find(
          (candidate) => candidate.sessionId === tab.sessionId
        );
        if (conversation) {
          codeApi.archiveConversation(conversation);
        }
      }
      return codeApi.removeTab(tabId);
    },
    restoreConversation: (sessionId: string) => {
      const list = persistedStoreApi.getKey('chatConversations') ?? [];
      persistedStoreApi.setKey(
        'chatConversations',
        list.map((conversation) =>
          conversation.sessionId === sessionId ? { ...conversation, archivedAt: undefined } : conversation
        )
      );
    },
    openSidecarApp: (tabId: CodeTabId, appId: string) => {
      if (appId === 'chat') {
        return;
      }
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((tab) => {
        if (tab.id !== tabId) {
          return tab;
        }
        const sidecarAppIds = tab.sidecarAppIds?.includes(appId)
          ? tab.sidecarAppIds
          : [...(tab.sidecarAppIds ?? []), appId];
        return { ...tab, sidecarOpen: true, sidecarAppIds, activeSidecarAppId: appId };
      });
      persistedStoreApi.setKey('codeTabs', tabs);
    },
    setSidecarOpen: (tabId: CodeTabId, sidecarOpen: boolean) => {
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((tab) =>
        tab.id === tabId ? { ...tab, sidecarOpen } : tab
      );
      persistedStoreApi.setKey('codeTabs', tabs);
    },
    setActiveSidecarApp: (tabId: CodeTabId, appId: string) => {
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((tab) =>
        tab.id === tabId && tab.sidecarAppIds?.includes(appId) ? { ...tab, activeSidecarAppId: appId } : tab
      );
      persistedStoreApi.setKey('codeTabs', tabs);
    },
    reorderSidecarApps: (tabId: CodeTabId, appIds: string[]) => {
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((tab) => {
        if (tab.id !== tabId || !tab.sidecarAppIds) {
          return tab;
        }
        // Accept only a permutation of the currently open set — a stale
        // drag result must not open or close apps as a side effect.
        const open = new Set(tab.sidecarAppIds);
        const next = [...new Set(appIds)].filter((id) => open.has(id));
        if (next.length !== tab.sidecarAppIds.length) {
          return tab;
        }
        return { ...tab, sidecarAppIds: next };
      });
      persistedStoreApi.setKey('codeTabs', tabs);
    },
    closeSidecarApp: (tabId: CodeTabId, appId: string) => {
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((tab) => {
        if (tab.id !== tabId || !tab.sidecarAppIds?.includes(appId)) {
          return tab;
        }
        const closedIndex = tab.sidecarAppIds.indexOf(appId);
        const sidecarAppIds = tab.sidecarAppIds.filter((id) => id !== appId);
        if (sidecarAppIds.length === 0) {
          const { sidecarAppIds: _open, activeSidecarAppId: _active, ...withoutSidecar } = tab;
          void _open;
          void _active;
          return { ...withoutSidecar, sidecarOpen: tab.sidecarOpen ?? true };
        }
        const activeSidecarAppId =
          tab.activeSidecarAppId !== appId && sidecarAppIds.includes(tab.activeSidecarAppId ?? '')
            ? tab.activeSidecarAppId
            : sidecarAppIds[Math.min(closedIndex, sidecarAppIds.length - 1)];
        return { ...tab, sidecarAppIds, activeSidecarAppId };
      });
      persistedStoreApi.setKey('codeTabs', tabs);
    },
    setTabSessionId: (tabId: CodeTabId, sessionId: string | undefined) => {
      // JSON transports encode an undefined positional argument as null.
      sessionId = sessionId ?? undefined;
      const current = (persistedStoreApi.getKey('codeTabs') ?? []).find((tab) => tab.id === tabId);
      if (current?.projectId && current.sessionId && current.sessionId !== sessionId && !current.routineId) {
        codeApi.recordConversation(current.sessionId);
      }
      const tabs = (persistedStoreApi.getKey('codeTabs') ?? []).map((t) => {
        if (t.id !== tabId) {
          return t;
        }
        if ((t.sessionId ?? undefined) === sessionId) {
          return { ...t, sessionId };
        }
        if (isChatColumn(t)) {
          // A fresh conversation returns the chat column to the lazy state —
          // greeting up, no sandbox until the first message.
          const { activatedAt: _reset, ...lazy } = t;
          void _reset;
          return { ...lazy, sessionId };
        }
        return { ...t, sessionId };
      });
      persistedStoreApi.setKey('codeTabs', tabs);
    },
  };
  return { codeApi, patch };
}
export type ChatOperations = ReturnType<typeof operations>['codeApi'];
export type ChatCommand = {
  [K in keyof ChatOperations]: { method: K; args: Parameters<ChatOperations[K]> };
}[keyof ChatOperations];
export type ChatCommandResult = ReturnType<ChatOperations[keyof ChatOperations]>;
export function applyChatCommand(
  state: StoreData,
  command: ChatCommand
): { patch: Partial<ChatState>; result: ChatCommandResult } {
  const { codeApi, patch } = operations(state);
  if (!Object.hasOwn(codeApi, command.method) || !Array.isArray(command.args)) {
    throw new Error('Invalid chat command');
  }
  const operation = codeApi[command.method] as (...args: unknown[]) => ChatCommandResult;
  const result = operation(...command.args);
  return { patch, result };
}
