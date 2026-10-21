import { expect, it, vi } from 'vitest';

import { ChatCleanupRunner, cleanupRemovedChat, completeChatRemoval } from './chat-removal';

const tab = { id: 'old-tab', sessionId: 'session', snapshotRef: 'old-workspace', projectId: null, createdAt: 1 };
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

it('retains failed jobs, drains others, and coalesces overlapping command and background retries', async () => {
  let jobs = [tab, { ...tab, id: 'neighbor' }];
  const cleanup = vi.fn(async (job: import('@/shared/types').CodeTab) => {
    if (job.id === tab.id) {
      throw new Error('offline');
    }
  });
  const acknowledge = vi.fn((id: string) => {
    jobs = jobs.filter((job) => job.id !== id);
  });
  const runner = new ChatCleanupRunner({ read: () => jobs, cleanup, acknowledge });
  await runner.drain();
  expect(jobs).toEqual([tab]);
  cleanup.mockImplementation(async () => {});
  const first = runner.runOne(tab);
  expect(runner.runOne(tab)).toBe(first);
  await first;
  expect(jobs).toEqual([]);
  expect(cleanup).toHaveBeenCalledTimes(3);
  await runner.dispose();
});

it('retries cleanup if its durable acknowledgement fails', async () => {
  const cleanup = vi.fn(async () => {});
  const acknowledge = vi.fn(async () => {}).mockRejectedValueOnce(new Error('database unavailable'));
  const runner = new ChatCleanupRunner({ read: () => [tab], cleanup, acknowledge });
  await expect(runner.runOne(tab)).rejects.toThrow('database unavailable');
  await runner.runOne(tab);
  expect(cleanup).toHaveBeenCalledTimes(2);
  expect(acknowledge).toHaveBeenCalledTimes(2);
  await runner.dispose();
});
