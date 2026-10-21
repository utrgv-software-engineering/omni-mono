import { beforeEach, describe, expect, it, vi } from 'vitest';

import { applyChatCommand, type ChatCommand } from '@/shared/chat-commands';
import type { CodeTab, Project, StoreData } from '@/shared/types';

const invoke = vi.fn((method: string, command?: unknown) => {
  if (method === 'store:chat-command') {
    const { patch, result } = applyChatCommand(store, command as ChatCommand);
    store = { ...store, ...patch };
    return Promise.resolve(result);
  }
  return Promise.resolve();
});
const setKey = vi.fn((key: keyof StoreData, value: StoreData[keyof StoreData]) => {
  store = { ...store, [key]: value } as StoreData;
  return Promise.resolve();
});
const getKey = vi.fn((key: keyof StoreData) => store[key]);

vi.mock('@/renderer/services/ipc', () => ({
  emitter: { invoke },
}));

vi.mock('@/renderer/services/store', () => ({
  persistedStoreApi: { getKey, setKey },
}));

vi.mock('@/renderer/services/agent-process', () => ({
  $agentStatuses: { get: () => ({}), set: vi.fn() },
  $agentXTerms: { get: () => ({}), set: vi.fn() },
  agentProcessApi: { start: vi.fn(), stop: vi.fn() },
  clearStatus: vi.fn(),
  pollProcessStatus: vi.fn(),
  teardownTerminal: vi.fn(),
}));

vi.mock('@/renderer/features/Console/state', () => ({
  destroyAllTerminalsForTab: vi.fn(),
  forgetTerminalsForTab: vi.fn(),
}));

vi.mock('@/renderer/constants', () => ({
  STATUS_POLL_INTERVAL_MS: 999999,
}));

let store: StoreData;

const project = (id: string, sandboxProfile?: string | null): Project => ({
  id,
  label: id,
  slug: id,
  sources: [],
  ...(sandboxProfile !== undefined ? { sandboxProfile } : {}),
  createdAt: 1,
});

const tab = (patch: Partial<CodeTab> = {}): CodeTab => ({
  id: 'tab-1',
  projectId: null,
  sessionId: 'session-1',
  createdAt: 1,
  ...patch,
});

const resetStore = (patch: Partial<StoreData> = {}) => {
  store = {
    projects: [],
    codeTabs: [],
    chatConversations: [],
    activeCodeTabId: null,
    ...patch,
  } as StoreData;
  invoke.mockClear();
  setKey.mockClear();
  getKey.mockClear();
};

describe('code tab sandbox profile resolution', () => {
  it('does not override a newer tile selection while closing another tile', async () => {
    const { codeApi } = await import('./state');
    resetStore({ codeTabs: [tab({ id: 'A' }), tab({ id: 'B' }), tab({ id: 'C' })], activeCodeTabId: 'A' });
    let saved!: () => void;
    invoke.mockImplementationOnce((_method, command) => {
      const { patch, result } = applyChatCommand(store, command as ChatCommand);
      store = { ...store, ...patch };
      return new Promise<typeof result>((resolve) => {
        saved = () => resolve(result);
      });
    });
    const closing = codeApi.removeTab('A');
    store = { ...store, activeCodeTabId: 'B' };
    saved();
    await closing;
    expect(store.activeCodeTabId).toBe('B');
  });
  beforeEach(() => {
    vi.resetModules();
    resetStore();
  });
});

describe('chat columns and conversation history', () => {
  beforeEach(() => {
    vi.resetModules();
    resetStore();
  });

  it('addTab creates a fresh tab instead of reusing an existing blank session', async () => {
    resetStore({ codeTabs: [tab({ id: 'blank-tab', projectId: null, sessionId: 'blank-session' })] });
    const { codeApi } = await import('./state');

    const created = await codeApi.addTab();

    expect(created.id).not.toBe('blank-tab');
    expect(store.codeTabs).toHaveLength(2);
    expect(store.activeCodeTabId).toBe(created.id);
  });

  it('addTab creates a fresh tab instead of reusing a routine session', async () => {
    resetStore({
      codeTabs: [
        tab({
          id: 'routine-tab',
          projectId: null,
          sessionId: 'routine-session',
          routineId: 'routine-1',
          routineName: 'Morning review',
          routineSchedule: 'Manual',
        }),
      ],
    });
    const { codeApi } = await import('./state');

    const created = await codeApi.addTab();

    expect(created.id).not.toBe('routine-tab');
    expect(store.codeTabs).toHaveLength(2);
    expect(store.activeCodeTabId).toBe(created.id);
    expect(store.codeTabs.find((item) => item.id === 'routine-tab')).toMatchObject({ routineId: 'routine-1' });
  });

  it('archiveTab archives the transcript and removes its column', async () => {
    resetStore({
      codeTabs: [tab({ id: 'chat-tab', projectId: null, sessionId: 'sess-1', activatedAt: 5 })],
      chatConversations: [{ sessionId: 'sess-1', title: 'Plan my week', lastActiveAt: 1 }],
    });
    const { codeApi } = await import('./state');

    await codeApi.archiveTab('chat-tab', 'Plan my week');

    expect(store.codeTabs).toHaveLength(0);
    expect(store.chatConversations[0]).toMatchObject({
      sessionId: 'sess-1',
      title: 'Plan my week',
      archivedAt: expect.any(Number),
    });
  });

  it('removeTab only removes technical columns and does not create session history', async () => {
    resetStore({
      codeTabs: [
        tab({ id: 'fresh-chat', projectId: null, sessionId: 'sess-fresh' }),
        tab({
          id: 'proj-tab',
          projectId: 'p1',
          sessionId: 'sess-proj',
        }),
      ],
    });
    const { codeApi } = await import('./state');

    await codeApi.removeTab('fresh-chat');
    await codeApi.removeTab('proj-tab');

    expect(store.chatConversations).toEqual([]);
  });

  it('setTabActivated stamps once and never re-stamps', async () => {
    resetStore({ codeTabs: [tab({ id: 'chat-tab', projectId: null, activatedAt: 42 })] });
    const { codeApi } = await import('./state');

    await codeApi.setTabActivated('chat-tab');

    expect(store.codeTabs[0]?.activatedAt).toBe(42);
  });

  it('setTabSessionId resets a chat column to the lazy state on a fresh conversation', async () => {
    resetStore({
      codeTabs: [tab({ id: 'chat-tab', projectId: null, sessionId: 'old', activatedAt: 5 })],
    });
    const { codeApi } = await import('./state');

    await codeApi.setTabSessionId('chat-tab', 'new');

    expect(store.codeTabs[0]).toMatchObject({ sessionId: 'new' });
    expect(store.codeTabs[0]?.activatedAt).toBeUndefined();
  });

  it('addTabForConversation activates an existing column showing that session', async () => {
    resetStore({ codeTabs: [tab({ id: 'chat-tab', projectId: null, sessionId: 'sess-1' })] });
    const { codeApi } = await import('./state');

    const opened = await codeApi.addTabForConversation({ sessionId: 'sess-1', title: 'x', lastActiveAt: 1 });

    expect(opened.id).toBe('chat-tab');
    expect(store.codeTabs).toHaveLength(1);
    expect(store.activeCodeTabId).toBe('chat-tab');
  });

  it('addTabForConversation rebuilds a column from retained history', async () => {
    resetStore();
    const { codeApi } = await import('./state');

    const opened = await codeApi.addTabForConversation({
      sessionId: 'sess-1',
      title: 'x',
      lastActiveAt: 1,
      projectId: 'project-1',
    });

    expect(opened).toMatchObject({
      projectId: 'project-1',
      sessionId: 'sess-1',
    });
    expect(opened.activatedAt).toBeTypeOf('number');
  });

  it('archives and restores retained conversations', async () => {
    const conversation = { sessionId: 'sess-1', title: 'x', lastActiveAt: 1 };
    resetStore({ chatConversations: [conversation] });
    const { codeApi } = await import('./state');

    await codeApi.archiveConversation(conversation);
    expect(store.chatConversations[0]).toMatchObject({ sessionId: 'sess-1', archivedAt: expect.any(Number) });

    await codeApi.restoreConversation('sess-1');
    expect(store.chatConversations[0]?.archivedAt).toBeUndefined();
  });

  it('reorderTabs preserves records missing from a filtered input list', async () => {
    const a = tab({ id: 'tab-a' });
    const b = tab({ id: 'tab-b' });
    const app = tab({ id: 'app-tab', routineId: 'routine-1' });
    resetStore({ codeTabs: [app, a, b] });
    const { codeApi } = await import('./state');

    await codeApi.reorderTabs([b, a]);

    expect(store.codeTabs.map((t) => t.id)).toEqual(['app-tab', 'tab-b', 'tab-a']);
  });
});
