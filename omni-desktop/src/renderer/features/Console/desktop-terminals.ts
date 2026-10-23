import { createTerminalStore, type TerminalTransport } from '@omniagents/chat-ui/terminal/terminal-store';

import { emitter, ipc } from '@/renderer/services/ipc';

/**
 * Terminals of the desktop's chat columns, scoped by column tab id. The main
 * process owns each shell's socket (`TerminalProxy`), so shells outlive a
 * renderer reload and come back through `terminal:list`.
 */
const ipcTransport: TerminalTransport = {
  list: (tabId) => emitter.invoke('terminal:list', tabId),
  create: (tabId) => emitter.invoke('terminal:create', tabId),
  dispose: (id) => emitter.invoke('terminal:dispose', id),
  write: (id, data) => void emitter.invoke('terminal:write', id, data),
  resize: (id, cols, rows) => void emitter.invoke('terminal:resize', id, cols, rows),
  subscribe: ({ output, exited }) => {
    const offOutput = ipc.on('terminal:output', output);
    const offExited = ipc.on('terminal:exited', exited);
    return () => {
      offOutput();
      offExited();
    };
  },
  // Main tags its errors as `[<kind>] <message>` (console-manager.ts).
  describeError: (error) => {
    const message = error instanceof Error ? error.message : String(error);
    return message.includes('[process_not_ready]') ? { kind: 'not_ready' } : { kind: 'unavailable', message };
  },
};

export const desktopTerminals = createTerminalStore(ipcTransport);
