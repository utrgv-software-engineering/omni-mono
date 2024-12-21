import type { Rectangle } from 'electron/main';
import type { Schema } from 'electron-store';

// Normally we'd use SWR or some query library, but I had some issues with react render cycles and the easiest fix
// was to just move all the fetching outside react. Also SWR doesn't narrow its data field when the request is
// successful, which is suuuuper annoying. This type provides a similar API, but better types. Just have to implement
// the fetching logic yourself.
export type AsyncRequest<T, E> =
  | {
      isUninitialized: true;
      isLoading: false;
      isError: false;
      isSuccess: false;
    }
  | {
      isUninitialized: false;
      isLoading: true;
      isError: false;
      isSuccess: false;
    }
  | {
      isUninitialized: false;
      isLoading: false;
      isError: true;
      isSuccess: false;
      error: E;
    }
  | {
      isUninitialized: false;
      isLoading: false;
      isError: false;
      isSuccess: true;
      data: T;
    };

export type WindowProps = {
  bounds: Rectangle;
  isMaximized: boolean;
  isFullScreen: boolean;
};

export type StoreData = {
  installDir?: string;
  serverMode: boolean;
  notifyForPrereleaseUpdates: boolean;
  launcherWindowProps?: WindowProps;
  appWindowProps?: WindowProps;
};

const winSizePropsSchema = {
  type: 'object',
  properties: {
    bounds: {
      type: 'object',
      properties: {
        x: { type: 'number' },
        y: { type: 'number' },
        width: { type: 'number' },
        height: { type: 'number' },
      },
    },
    isMaximized: { type: 'boolean' },
    isFullScreen: { type: 'boolean' },
  },
};

export const schema: Schema<StoreData> = {
  installDir: {
    type: 'string',
  },
  serverMode: {
    type: 'boolean',
    default: false,
  },
  notifyForPrereleaseUpdates: {
    type: 'boolean',
    default: true,
  },
  launcherWindowProps: winSizePropsSchema,
  appWindowProps: winSizePropsSchema,
};

export type GpuType = 'nvidia<30xx' | 'nvidia>=30xx' | 'amd' | 'nogpu';
export const GPU_TYPE_MAP: Record<GpuType, string> = {
  'nvidia<30xx': 'Nvidia (20xx and below)',
  'nvidia>=30xx': 'Nvidia (30xx and above)',
  amd: 'AMD',
  nogpu: 'No GPU',
};
export type OperatingSystem = 'Windows' | 'macOS' | 'Linux';

type Namespaced<Prefix extends string, T, Sep extends string = ':'> = {
  [K in keyof T as `${Prefix}${Sep}${string & K}`]: T[K];
};

export type DirDetails =
  // Directory is installed and can be launched
  | {
      path: string;
      isInstalled: true;
      canInstall: true;
      isDirectory: true;
      isFirstRun: boolean;
      version: string;
      invokeExecPath: string;
      activateVenvPath: string;
    }
  // Directory is not installed but can be installed
  | {
      path: string;
      isDirectory: true;
      isInstalled: false;
      canInstall: true;
    }
  // Directory is not installed and can not be installed
  | {
      path: string;
      isDirectory: false;
      isInstalled: false;
      canInstall: false;
    };

export type InstallType =
  | {
      type: 'fresh';
      newVersion: string;
    }
  | {
      type: 'reinstall' | 'upgrade' | 'downgrade';
      newVersion: string;
      installedVersion: string;
    };

type OkStatus<StatusType extends string, Data = void> = Data extends void
  ? {
      type: StatusType;
    }
  : {
      type: StatusType;
      data: Data;
    };
type ErrorStatus = {
  type: 'error';
  error: {
    message: string;
    context?: Record<string, unknown>;
  };
};

export type Status<State extends string> = OkStatus<State> | ErrorStatus;

export type MainProcessStatus = Status<'initializing' | 'idle' | 'exiting'>;
export type InstallProcessStatus = Status<
  'uninitialized' | 'starting' | 'installing' | 'canceling' | 'exiting' | 'completed' | 'canceled'
>;
export type InvokeProcessStatus =
  | Status<'uninitialized' | 'starting' | 'exiting' | 'exited'>
  | OkStatus<'running', { url: string }>;

type LogLevel = 'debug' | 'info' | 'warn' | 'error';
export type LogEntry = {
  level: LogLevel;
  message: string;
};

export type WithTimestamp<T> = T & { timestamp: number };

export type PtyOptions = {
  cwd?: string;
  cmd?: string[];
  size?: { cols: number; rows: number };
};

type StoreIpcEvents = Namespaced<
  'store',
  {
    'get-key': <K extends keyof StoreData>(key: K) => StoreData[K];
    'set-key': <K extends keyof StoreData>(key: K, val: StoreData[K]) => void;
    get: () => StoreData;
    set: (data: StoreData) => void;
    reset: () => void;
  }
>;

type MainProcessIpcEvents = Namespaced<
  'main-process',
  {
    'get-status': () => WithTimestamp<MainProcessStatus>;
    exit: () => void;
  }
>;

type InstallProcessIpcEvents = Namespaced<
  'install-process',
  {
    'get-status': () => WithTimestamp<InstallProcessStatus>;
    'start-install': (location: string, gpuType: GpuType, version: string) => void;
    'cancel-install': () => void;
  }
>;

type InvokeProcessIpcEvents = Namespaced<
  'invoke-process',
  {
    'get-status': () => WithTimestamp<InvokeProcessStatus>;
    'start-invoke': (location: string) => void;
    'exit-invoke': () => void;
  }
>;

type UtilIpcEvents = Namespaced<
  'util',
  {
    'select-directory': (path?: string) => string | null;
    'get-home-directory': () => string;
    'get-is-directory': (path: string) => boolean;
    'get-is-file': (path: string) => boolean;
    'get-path-exists': (path: string) => boolean;
    'get-os': () => OperatingSystem;
    'get-dir-details': (path: string) => DirDetails;
    'get-default-install-dir': () => string;
    'open-directory': (path: string) => string;
  }
>;

type TerminalIpcEvents = Namespaced<
  'terminal',
  {
    replay: (id: string) => string | null;
    create: (cwd?: string) => string;
    list: () => string[];
    write: (id: string, data: string) => void;
    resize: (id: string, cols: number, rows: number) => void;
    dispose: (id: string) => void;
  }
>;

// Main process ipc events
export type IpcEvents = MainProcessIpcEvents &
  InstallProcessIpcEvents &
  InvokeProcessIpcEvents &
  UtilIpcEvents &
  TerminalIpcEvents &
  StoreIpcEvents;

type StoreIpcRendererEvents = Namespaced<
  'store',
  {
    changed: [StoreData | undefined];
  }
>;

type TerminalIpcRendererEvents = Namespaced<
  'terminal',
  {
    output: [string, string];
    exited: [string, number];
  }
>;
type MainProcessIpcRendererEvents = Namespaced<
  'main-process',
  {
    status: [WithTimestamp<MainProcessStatus>];
  }
>;

type InstallProcessIpcRendererEvents = Namespaced<
  'install-process',
  {
    status: [WithTimestamp<InstallProcessStatus>];
    log: [WithTimestamp<LogEntry>];
  }
>;

type InvokeProcessIpcRendererEvents = Namespaced<
  'invoke-process',
  {
    status: [WithTimestamp<InvokeProcessStatus>];
    log: [WithTimestamp<LogEntry>];
  }
>;

type DevIpcRendererEvents = Namespaced<
  'dev',
  {
    'console-log': [unknown];
  }
>;

//Renderer ipc events
export type IpcRendererEvents = TerminalIpcRendererEvents &
  MainProcessIpcRendererEvents &
  InstallProcessIpcRendererEvents &
  InvokeProcessIpcRendererEvents &
  DevIpcRendererEvents &
  StoreIpcRendererEvents;
