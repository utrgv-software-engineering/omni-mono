import { map } from 'nanostores';

import { STATUS_POLL_INTERVAL_MS } from '@/renderer/constants';
import type { AutoLaunchPhase } from '@/renderer/features/Code/use-code-auto-launch';
import { forgetTerminalsForTab } from '@/renderer/features/Console/state';
import { clearStatus, pollProcessStatus, teardownTerminal } from '@/renderer/services/agent-process';
import { emitter } from '@/renderer/services/ipc';
import { persistedStoreApi } from '@/renderer/services/store';
import type { ChatCommand, ChatOperations } from '@/shared/chat-commands';
import type { CodeTab, CodeTabId } from '@/shared/types';

export const $codeTabPhases = map<Record<CodeTabId, AutoLaunchPhase>>({});
export const $codeTabErrors = map<Record<CodeTabId, string | null>>({});

export function finishRemoval(tabId: string) {
  try {
    forgetTerminalsForTab(tabId);
    teardownTerminal(tabId);
  } finally {
    clearStatus(tabId);
    const phases = { ...$codeTabPhases.get() };
    delete phases[tabId];
    $codeTabPhases.set(phases);
    const errors = { ...$codeTabErrors.get() };
    delete errors[tabId];
    $codeTabErrors.set(errors);
  }
}
function run<K extends keyof ChatOperations>(
  method: K,
  ...args: Parameters<ChatOperations[K]>
): Promise<ReturnType<ChatOperations[K]>> {
  return emitter.invoke('store:chat-command', { method, args } as ChatCommand) as Promise<
    ReturnType<ChatOperations[K]>
  >;
}
export const codeApi = {
  openFreshChat: (...args: Parameters<ChatOperations['openFreshChat']>) => run('openFreshChat', ...args),
  addTab: (...args: Parameters<ChatOperations['addTab']>) => run('addTab', ...args),
  removeTab: async (...args: Parameters<ChatOperations['removeTab']>): Promise<void> => {
    await run('removeTab', ...args);
    finishRemoval(args[0]);
  },
  setActiveTab: (...args: Parameters<ChatOperations['setActiveTab']>) => run('setActiveTab', ...args),
  setLayoutMode: (...args: Parameters<ChatOperations['setLayoutMode']>) => run('setLayoutMode', ...args),
  setSpacesColumnLayouts: (...args: Parameters<ChatOperations['setSpacesColumnLayouts']>) =>
    run('setSpacesColumnLayouts', ...args),
  reorderTabs: (tabs: CodeTab[]) =>
    run(
      'reorderTabs',
      tabs.map((tab) => tab.id)
    ),
  setTabProject: (...args: Parameters<ChatOperations['setTabProject']>) => run('setTabProject', ...args),
  setTabActivated: (...args: Parameters<ChatOperations['setTabActivated']>) => run('setTabActivated', ...args),
  addTabForConversation: (...args: Parameters<ChatOperations['addTabForConversation']>) =>
    run('addTabForConversation', ...args),
  recordConversation: (...args: Parameters<ChatOperations['recordConversation']>) => run('recordConversation', ...args),
  archiveConversation: (...args: Parameters<ChatOperations['archiveConversation']>) =>
    run('archiveConversation', ...args),
  archiveTab: async (...args: Parameters<ChatOperations['archiveTab']>): Promise<void> => {
    await run('archiveTab', ...args);
    finishRemoval(args[0]);
  },
  restoreConversation: (...args: Parameters<ChatOperations['restoreConversation']>) =>
    run('restoreConversation', ...args),
  openSidecarApp: (...args: Parameters<ChatOperations['openSidecarApp']>) => run('openSidecarApp', ...args),
  setSidecarOpen: (...args: Parameters<ChatOperations['setSidecarOpen']>) => run('setSidecarOpen', ...args),
  setActiveSidecarApp: (...args: Parameters<ChatOperations['setActiveSidecarApp']>) =>
    run('setActiveSidecarApp', ...args),
  reorderSidecarApps: (...args: Parameters<ChatOperations['reorderSidecarApps']>) => run('reorderSidecarApps', ...args),
  closeSidecarApp: (...args: Parameters<ChatOperations['closeSidecarApp']>) => run('closeSidecarApp', ...args),
  setTabSessionId: (...args: Parameters<ChatOperations['setTabSessionId']>) => run('setTabSessionId', ...args),
};
const listen = () => {
  // Poll code tab statuses (chat polling is handled by agent-process service)
  const pollStatuses = async () => {
    const tabs = persistedStoreApi.getKey('codeTabs') ?? [];
    for (const tab of tabs) {
      await pollProcessStatus(tab.id);
    }
  };

  setInterval(pollStatuses, STATUS_POLL_INTERVAL_MS);
};

listen();
