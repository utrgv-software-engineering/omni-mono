import { isEqual } from 'lodash-es';
import { atom } from 'nanostores';

import { POLL_INTERVAL } from '@/renderer/constants';
import { emitter, ipc } from '@/renderer/services/ipc';
import type { MainProcessStatus, WithTimestamp } from '@/shared/types';

const $mainProcessStatus = atom<WithTimestamp<MainProcessStatus>>({
  type: 'initializing',
  timestamp: Date.now(),
});

const listen = () => {
  ipc.on('main-process:status', (_, data) => {
    $mainProcessStatus.set(data);
  });

  const poll = async () => {
    const oldStatus = $mainProcessStatus.get();
    const newStatus = await emitter.invoke('main-process:get-status');
    if (isEqual(oldStatus, newStatus)) {
      return;
    }
    $mainProcessStatus.set(newStatus);
  };

  setInterval(poll, POLL_INTERVAL);
};

listen();
