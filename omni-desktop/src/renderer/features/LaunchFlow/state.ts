import { isEqual } from 'lodash-es';
import { atom, computed } from 'nanostores';

import { LineBuffer } from '@/lib/line-buffer';
import { POLL_INTERVAL } from '@/renderer/constants';
import { emitter, ipc } from '@/renderer/services/ipc';
import { persistedStoreApi, syncInstallDirDetails } from '@/renderer/services/store';
import type { InvokeProcessStatus, LogEntry, WithTimestamp } from '@/shared/types';

export const getIsInvokeProcessActive = (status: InvokeProcessStatus) => {
  switch (status.type) {
    case 'running':
    case 'starting':
    case 'exiting':
      return true;
    default:
      return false;
  }
};

export const $invokeProcessStatus = atom<WithTimestamp<InvokeProcessStatus>>({
  type: 'uninitialized',
  timestamp: Date.now(),
});
export const $isInvokeProcessActive = computed($invokeProcessStatus, getIsInvokeProcessActive);
export const $isInvokeProcessPendingDismissal = atom(false);

$invokeProcessStatus.subscribe((status, oldStatus) => {
  if (oldStatus && getIsInvokeProcessActive(oldStatus) && !getIsInvokeProcessActive(status)) {
    $isInvokeProcessPendingDismissal.set(true);
  }
});

export const $invokeProcessLogs = atom<WithTimestamp<LogEntry>[]>([]);

const listen = () => {
  const buffer = new LineBuffer({ stripAnsi: true });

  ipc.on('invoke-process:log', (_, data) => {
    const buffered = buffer.append(data.message);
    for (const message of buffered) {
      $invokeProcessLogs.set([...$invokeProcessLogs.get(), { ...data, message }]);
    }
  });

  ipc.on('invoke-process:status', (_, status) => {
    $invokeProcessStatus.set(status);
    if (status.type === 'exited' || status.type === 'error') {
      // Flush the buffer when the process exits in case there were any remaining logs
      buffer.flush();

      // If the invoke process errored, we need to double-check the install dir to make sure it's still valid
      const installDir = persistedStoreApi.$atom.get().installDir;
      if (installDir) {
        syncInstallDirDetails(installDir);
      }
    }
  });

  const poll = async () => {
    const oldStatus = $invokeProcessStatus.get();
    const newStatus = await emitter.invoke('invoke-process:get-status');
    if (isEqual(oldStatus, newStatus)) {
      return;
    }
    $invokeProcessStatus.set(newStatus);
  };

  setInterval(poll, POLL_INTERVAL);
};

listen();
