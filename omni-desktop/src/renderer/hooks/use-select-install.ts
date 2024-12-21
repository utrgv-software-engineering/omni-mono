import { useCallback } from 'react';

import { emitter } from '@/renderer/services/ipc';
import { persistedStoreApi } from '@/renderer/services/store';

export const useSelectInstall = () => {
  const selectInstall = useCallback(async () => {
    const installDir = persistedStoreApi.getKey('installDir');
    const newInstallDir = await emitter.invoke('util:select-directory', installDir);
    if (newInstallDir) {
      persistedStoreApi.setKey('installDir', newInstallDir);
    }
  }, []);

  return selectInstall;
};
