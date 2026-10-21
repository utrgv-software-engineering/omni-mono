import type { ChatCommand } from '@/shared/chat-commands';
import type { CodeTab } from '@/shared/types';

/** Run at the authoritative command boundary, before replying to a renderer.
 * A lost reply or closing window must not skip runtime/workspace cleanup. */
export async function completeChatRemoval(
  command: ChatCommand,
  result: unknown,
  cleanup: (tab: CodeTab) => Promise<void>
) {
  if ((command.method === 'removeTab' || command.method === 'archiveTab') && result) {
    await cleanup(result as CodeTab);
  }
}

export async function cleanupRemovedChat(
  tab: CodeTab,
  deps: {
    disposeTerminals?: (id: string) => Promise<void>;
    stop: (id: string) => Promise<unknown>;
  }
) {
  let terminalFailure: unknown;
  try {
    await deps.disposeTerminals?.(tab.id);
  } catch (error) {
    terminalFailure = error;
  }
  // Terminal failure must not leave an invisible runtime running.
  await deps.stop(tab.id);
  if (terminalFailure) {
    throw terminalFailure;
  }
}
