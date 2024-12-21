import { IpcEmitter, IpcListener } from '@electron-toolkit/typed-ipc/renderer';

import type { IpcEvents, IpcRendererEvents } from '@/shared/types';

export const ipc = new IpcListener<IpcRendererEvents>();
export const emitter = new IpcEmitter<IpcEvents>();
