import { useEffect, useState, type ComponentProps } from "react";
import { appTransport } from "./transport";
import { factoryUrl } from "../factory-scope";
import { inTeams, protectedFile } from "../teams";

export function useAsset(source: string) {
  const [resolved, setResolved] = useState<string>();
  const transport = appTransport();
  const url = new URL(source || "/", window.location.href);
  const teams = inTeams() && url.origin === location.origin && protectedFile(url.pathname);
  useEffect(() => {
    if ((!transport && !teams) || !source) return;
    const origin = transport?.origin ?? location.origin;
    const parsed = new URL(source, origin);
    if (parsed.origin !== origin) return;
    let path = parsed.pathname + parsed.search;
    if (transport?.factory && path.startsWith(transport.factory + "/")) path = path.slice(transport.factory.length);
    let disposed = false;
    let objectUrl: string | undefined;
    void (transport ? transport.request(path) : fetch(path)).then(async response => {
      if (!response.ok) throw new Error("Attachment is unavailable");
      const blob = await response.blob();
      if (!disposed) { objectUrl = URL.createObjectURL(blob); setResolved(objectUrl); }
    }).catch(() => setResolved(undefined));
    return () => { disposed = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [source, transport, teams]);
  return transport || teams ? resolved : factoryUrl(source);
}
export function AppImage({ src = "", ...props }: ComponentProps<"img">) {
  return <img {...props} src={useAsset(src)} />;
}
export function AppVideo({ src = "", ...props }: ComponentProps<"video">) {
  return <video {...props} src={useAsset(src)} />;
}
