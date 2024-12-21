import type { ReadableAtom } from 'nanostores';
import { atom } from 'nanostores';

import { emitter, ipc } from '@/renderer/services/ipc';
import type { DirDetails, OperatingSystem, StoreData } from '@/shared/types';

const getDefaults = (): StoreData => ({ serverMode: false, notifyForPrereleaseUpdates: true });

const _$store = atom<StoreData>(getDefaults());

ipc.on('store:changed', (_, data) => {
  _$store.set(data ?? getDefaults());
});

export const persistedStoreApi = {
  $atom: _$store as ReadableAtom<StoreData>,
  setKey: <K extends keyof StoreData>(key: K, value: StoreData[K]): Promise<void> => {
    return emitter.invoke('store:set-key', key, value);
  },
  getKey: <K extends keyof StoreData>(key: K): StoreData[K] => {
    return _$store.get()[key];
  },
  set: (data: StoreData): Promise<void> => {
    return emitter.invoke('store:set', data);
  },
  get: (): StoreData => {
    return _$store.get();
  },
  reset: (): Promise<void> => {
    return emitter.invoke('store:reset');
  },
  sync: async () => {
    const data = await emitter.invoke('store:get');
    _$store.set(data);
  },
};

export const $initialized = atom(false);
export const $installDirDetails = atom<DirDetails | undefined>(undefined);

export const syncInstallDirDetails = async (installDir: string) => {
  const newDirDetails = await emitter.invoke('util:get-dir-details', installDir);
  $installDirDetails.set(newDirDetails);
};

persistedStoreApi.$atom.listen(() => {
  const installDir = persistedStoreApi.$atom.get().installDir;
  const dirDetails = $installDirDetails.get();

  if (!installDir) {
    $installDirDetails.set(undefined);
  } else if (installDir !== dirDetails?.path) {
    syncInstallDirDetails(installDir);
  }

  $initialized.set(true);
});

export const $operatingSystem = atom<OperatingSystem | undefined>(undefined);
emitter.invoke('util:get-os').then($operatingSystem.set);

persistedStoreApi.sync();
