import { IpcEmitter, IpcListener } from '@electron-toolkit/typed-ipc/renderer';

import type { IpcEvents, IpcRendererEvents } from '@/shared/types';

/**
 * A typed IPC listener for the renderer process.
 */
export const ipc = new IpcListener<IpcRendererEvents>();
/**
 * A typed IPC emitter for the renderer process.
 */
export const emitter = new IpcEmitter<IpcEvents>();
