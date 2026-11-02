import { factoryFetch, factoryUrl } from "./factory-scope";
import { useEffect, useRef, useState } from "react";
import RFB from "@novnc/novnc";
import { Button } from "@omniagents/ui/button";

export function useDesktopAvailable(enabled: boolean) {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    if (!enabled) {
      setAvailable(false);
      return;
    }
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const response = await factoryFetch("/workplace/api/desktop", {
          signal: controller.signal,
        });
        const data = response.ok ? await response.json() : {};
        if (!controller.signal.aborted) setAvailable(data.available === true);
      } catch {
        if (!controller.signal.aborted) setAvailable(false);
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [enabled]);
  return available;
}

export function Desktop() {
  const target = useRef<HTMLDivElement>(null);
  const [connected, setConnected] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!target.current) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const url = new URL(factoryUrl("/workplace/api/desktop/ws"), window.location.href);
    url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const rfb = new RFB(target.current, url.href);
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.addEventListener("connect", () => setConnected(true));
    rfb.addEventListener("disconnect", () => {
      if (disposed) return;
      setConnected(false);
      timer = setTimeout(() => setAttempt((value) => value + 1), 3000);
    });
    return () => {
      disposed = true;
      clearTimeout(timer);
      rfb.disconnect();
    };
  }, [attempt]);
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {!connected && (
        <div
          role="status"
          className="flex items-center gap-2 p-3 text-sm text-muted-foreground"
        >
          Connecting to desktop…
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Reconnect
          </Button>
        </div>
      )}
      <div
        ref={target}
        aria-label="VM desktop"
        className="min-h-0 flex-1 overflow-hidden"
      />
    </div>
  );
}
