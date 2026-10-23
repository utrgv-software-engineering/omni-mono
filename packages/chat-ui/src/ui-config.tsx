import type { ReactNode } from 'react';
import { createContext, useContext, useMemo } from 'react';

/** Where the agent runtime's GUI server is and how to authenticate to it. */
export type AgentRuntimeConnection = {
  /** Absolute, or relative to the page. */
  baseUrl: string;
  authToken?: string;
};

export type UiConfig = {
  runtimeBaseUrl: string;
  url: URL;
  searchParams: URLSearchParams;
  token?: string;
  debug: boolean;
  minimal: boolean;
  session?: string;
  wsBaseUrl: string;
  wsRealtimeUrl: string;
  wsOrigin: string;
  httpBaseUrl: string;
  /** Where the runtime is mounted on its host: '' on its own port,
   * `/proxy/<name>` behind the desktop's proxy, a mount path inside another
   * server (Studio). */
  basePath: string;
  resolvePath: (path: string) => string;
};

const UiConfigContext = createContext<UiConfig | null>(null);

/** The base URL names the runtime's directory: everything up to its last
 * `/`, so `/proxy/chat/` and `/proxy/chat/index.html` are both `/proxy/chat`. */
const basePathOf = (pathname: string): string => pathname.slice(0, pathname.lastIndexOf('/'));

const buildWsUrl = (url: URL, path: string): string => {
  const proto = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${url.host}${path}`;
};

export const resolveUiConfig = (connection: AgentRuntimeConnection): UiConfig => {
  // Downstream (wsBaseUrl, wsRealtimeUrl, httpBaseUrl, basePath) all
  // derive from url.host, so this one resolution decides every URL the RPC
  // client, realtime voice and terminals use.
  const url = new URL(connection.baseUrl, window.location.href);
  const searchParams = url.searchParams;
  // Launcher-owned runtimes pass credentials as typed process data. Keep
  // query-token parsing only for standalone/browser entry points that still
  // receive an authenticated UI handoff URL.
  const token = connection.authToken ?? searchParams.get('token') ?? undefined;
  const debug = /^(1|true|yes)$/i.test(String(searchParams.get('debug') || ''));
  const minimal = searchParams.get('minimal') === 'true';
  const session = searchParams.get('session') || undefined;
  const basePath = basePathOf(url.pathname);
  // Server-reported paths (`/ws/terminal`) are relative to the runtime.
  const resolvePath = (path: string) => (path.startsWith(`${basePath}/`) ? path : `${basePath}${path}`);
  const httpBaseUrl = `${url.protocol}//${url.host}`;
  const wsOrigin = buildWsUrl(url, '');
  const wsBaseUrl = `${wsOrigin}${resolvePath('/ws')}`;
  const wsRealtimeUrl = `${wsOrigin}${resolvePath('/ws/realtime')}`;
  return {
    runtimeBaseUrl: connection.baseUrl,
    url,
    searchParams,
    token,
    debug,
    minimal,
    session,
    wsBaseUrl,
    wsRealtimeUrl,
    wsOrigin,
    httpBaseUrl,
    basePath,
    resolvePath,
  };
};

export const UiConfigProvider = ({
  connection,
  children,
}: {
  connection: AgentRuntimeConnection;
  children: ReactNode;
}) => {
  const value = useMemo<UiConfig>(() => resolveUiConfig(connection), [connection.authToken, connection.baseUrl]);

  return <UiConfigContext.Provider value={value}>{children}</UiConfigContext.Provider>;
};

export const useUiConfig = (): UiConfig => {
  const ctx = useContext(UiConfigContext);
  if (!ctx) {
    throw new Error('UiConfigProvider is missing');
  }
  return ctx;
};
