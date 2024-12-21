import { useStore } from '@nanostores/react';
import { useCallback } from 'react';

import { initializeTerminal } from '@/renderer/features/Console/state';
import { $installDirDetails } from '@/renderer/services/store';

export const useNewTerminal = () => {
  const installDir = useStore($installDirDetails);
  const newTerminal = useCallback(() => {
    const cwd = installDir && installDir.isInstalled ? installDir.path : undefined;
    initializeTerminal(cwd);
  }, [installDir]);

  return newTerminal;
};
