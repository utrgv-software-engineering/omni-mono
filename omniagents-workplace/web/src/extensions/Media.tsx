import { useEffect, useState, type ComponentProps } from "react";
import { appTransport } from "./transport";
import { factoryUrl } from "../factory-scope";

function useAsset(source: string) {
  const [resolved, setResolved] = useState<string>();
  const transport = appTransport();
  useEffect(() => {
    if (!transport || !source) return;
    const parsed = new URL(source, transport.origin);
    if (parsed.origin !== transport.origin) return;
    let path = parsed.pathname + parsed.search;
    if (transport.factory && path.startsWith(transport.factory + "/")) path = path.slice(transport.factory.length);
    let disposed = false;
    let objectUrl: string | undefined;
    void transport.request(path).then(async response => {
      if (!response.ok) throw new Error("Attachment is unavailable");
      const blob = await response.blob();
      if (!disposed) { objectUrl = URL.createObjectURL(blob); setResolved(objectUrl); }
    }).catch(() => setResolved(undefined));
    return () => { disposed = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [source, transport]);
  return transport ? resolved : factoryUrl(source);
}
export function AppImage({ src = "", ...props }: ComponentProps<"img">) {
  return <img {...props} src={useAsset(src)} />;
}
export function AppVideo({ src = "", ...props }: ComponentProps<"video">) {
  return <video {...props} src={useAsset(src)} />;
}
