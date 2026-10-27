import { expect, it, vi } from 'vitest';

import { cleanupRemovedChat, completeChatRemoval } from './chat-removal';

const tab = { id: 'old-tab', sessionId: 'session', projectId: null, createdAt: 1 };
const deps = () => ({
  disposeTerminals: vi.fn(async (_id: string) => {}),
  stop: vi.fn(async (_id: string) => {}),
});

it('finishes authoritative cleanup even if the renderer never receives the result', async () => {
  const cleanup = deps();
  const completed = completeChatRemoval({ method: 'archiveTab', args: [tab.id] }, tab, (removed) =>
    cleanupRemovedChat(removed, cleanup)
  );
  // The tab store commit precedes this operation; no renderer continuation
  // participates in stopping the old runtime.
  await completed;
  expect(cleanup.stop).toHaveBeenCalledExactlyOnceWith('old-tab');
});

it('still shuts down the runtime when terminal disposal rejects', async () => {
  const cleanup = deps();
  cleanup.disposeTerminals.mockRejectedValueOnce(new Error('terminal offline'));
  await expect(cleanupRemovedChat(tab, cleanup)).rejects.toThrow('terminal offline');
  expect(cleanup.stop).toHaveBeenCalledExactlyOnceWith('old-tab');
});

it('does not run cleanup for unrelated commands or an already removed tab', async () => {
  const cleanup = vi.fn();
  await completeChatRemoval({ method: 'addTab', args: [] }, tab, cleanup);
  await completeChatRemoval({ method: 'removeTab', args: [tab.id] }, undefined, cleanup);
  expect(cleanup).not.toHaveBeenCalled();
});
