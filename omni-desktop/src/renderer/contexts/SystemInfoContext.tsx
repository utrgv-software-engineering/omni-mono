import { useStore } from '@nanostores/react';
import type { PropsWithChildren } from 'react';
import { createContext, memo, useContext, useMemo } from 'react';
import { assert } from 'tsafe';

import { LoaderFullScreen } from '@/renderer/common/LoaderFullScreen';
import { $initialized, $operatingSystem } from '@/renderer/services/store';
import type { OperatingSystem } from '@/shared/types';

type SystemInfo = {
  operatingSystem?: OperatingSystem;
  initialized: boolean;
};

const SystemInfoContext = createContext<SystemInfo>({ initialized: false });

const isCtxReady = (ctx: SystemInfo): ctx is Required<SystemInfo> => {
  return ctx.operatingSystem !== undefined && ctx.initialized === true;
};

export const SystemInfoProvider = memo((props: PropsWithChildren) => {
  const operatingSystem = useStore($operatingSystem);
  const initialized = useStore($initialized);

  const systemInfo = useMemo<SystemInfo>(() => ({ operatingSystem, initialized }), [initialized, operatingSystem]);

  return <SystemInfoContext.Provider value={systemInfo}>{props.children}</SystemInfoContext.Provider>;
});
SystemInfoProvider.displayName = 'SystemInfoProvider';

export const SystemInfoLoadingGate = memo((props: PropsWithChildren) => {
  const ctx = useContext(SystemInfoContext);
  if (!isCtxReady(ctx)) {
    return <LoaderFullScreen />;
  }
  return props.children;
});
SystemInfoLoadingGate.displayName = 'SystemInfoGate';

export const useSystemInfo = () => {
  const ctx = useContext(SystemInfoContext);
  assert(isCtxReady(ctx), 'SystemInfo not ready. Did you forget to wrap your component with SystemInfoGate?');
  return ctx;
};
