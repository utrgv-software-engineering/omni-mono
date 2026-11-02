import { appTransport } from "./extensions/transport";
/** Explicit URL scope; never select a factory using a mutable cookie. */
export function factoryBase(): string {
  if (appTransport()) return appTransport()!.factory;
  return (
    window.location.pathname.match(/^\/factories\/[a-f0-9]{32}(?=\/|$)/)?.[0] ??
    ""
  );
}

export function factoryPath(): string {
  if (appTransport()) return appTransport()!.path;
  return window.location.pathname.slice(factoryBase().length) || "/";
}

export function factoryUrl(value: string): string {
  const embedded = appTransport();
  if (embedded && value.startsWith("/") && !value.startsWith("//")) return embedded.origin + (value.startsWith("/factories/") ? value : embedded.factory + value);
  const base = factoryBase();
  if (!base || !value || value.startsWith("//")) return value;
  if (value.startsWith("/") && !value.startsWith("/factories/"))
    return base + value;
  try {
    const url = new URL(value);
    if (
      url.host === window.location.host &&
      ["http:", "https:", "ws:", "wss:"].includes(url.protocol) &&
      !url.pathname.startsWith("/factories/")
    ) {
      url.pathname = base + url.pathname;
      return url.href;
    }
  } catch {
    /* relative resource or non-URL text */
  }
  return value;
}

export function factoryFetch(input: string, init?: RequestInit) {
  return appTransport()?.request(input, init) ?? fetch(factoryUrl(input), init);
}

export function factoryStorageKey(key: string) {
  return factoryBase() ? `${factoryBase()}:${key}` : key;
}
