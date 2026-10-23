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
  proxyPrefix: string | null;
  resolvePath: (path: string) => string;
};

const UiConfigContext = createContext<UiConfig | null>(null);

const getProxyPrefix = (pathname: string): string | null => {
  if (!pathname.startsWith('/proxy/')) {
    return null;
  }
  const parts = pathname.split('/').filter(Boolean); // ['proxy', <name>, ...]
  if (parts.length < 2) {
    return null;
  }
  return `/proxy/${parts[1]}`;
};

const buildWsUrl = (url: URL, path: string): string => {
  const proto = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${url.host}${path}`;
};

export const resolveUiConfig = (connection: AgentRuntimeConnection): UiConfig => {
  // Downstream (wsBaseUrl, wsRealtimeUrl, httpBaseUrl, proxyPrefix) all
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
  const proxyPrefix = getProxyPrefix(url.pathname);
  const resolvePath = (path: string) => {
    if (!proxyPrefix || path.startsWith('/proxy/')) {
      return path;
    }
    return `${proxyPrefix}${path}`;
  };
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
    proxyPrefix,
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
