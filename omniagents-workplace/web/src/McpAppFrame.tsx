import { useEffect, useRef, useState } from "react";
import { AppBridge, PostMessageTransport, buildAllowAttribute, type McpUiResourceCsp, type McpUiResourcePermissions } from "@modelcontextprotocol/ext-apps/app-bridge";
import type { CallToolResult, ReadResourceResult } from "@modelcontextprotocol/sdk/types.js";
import { factoryFetch } from "./factory-scope";
import { subscribe } from "./api";

export interface AppEntrypoint { tool: string; name: string; uri: string; icons?: { src: string }[] }
export interface InstalledApp {
  id: string; name: string; kind: "builtin" | "remote" | "native"; enabled: boolean; showInNavigation?: boolean;
  sandboxOrigin?: string; path?: string; icon?: string; url?: string; permissions: Record<string, boolean>;
  entrypoints: AppEntrypoint[];
}
export interface AppCatalog { apps: InstalledApp[]; sandboxOrigin: string; nativeNavigation?: Record<string, boolean> }
export async function extensionRequest<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const response = await factoryFetch(`/workplace/api/extensions${path}`, body === undefined ? undefined : {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `App request failed (${response.status})`);
  }
  return response.json();
}

export function McpAppFrame({ app, entry, sandboxOrigin, path, navigate }: {
  app: InstalledApp; entry: AppEntrypoint; sandboxOrigin: string; path: string; navigate: (path: string) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<AppBridge | null>(null);
  const route = useRef(path);
  route.current = path;
  const [error, setError] = useState("");
  useEffect(() => {
    const iframe = frame.current!;
    const isolatedOrigin = app.sandboxOrigin ?? sandboxOrigin;
    const controller = new AbortController();
    let bridge: AppBridge | undefined;
    let unsubscribe: (() => void) | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposeReady: (() => void) | undefined;
    let resize: ResizeObserver | undefined;
    let theme: MutationObserver | undefined;
    setError("");
    const start = async () => {
      const opened = await extensionRequest<{ result: CallToolResult; resource: ReadResourceResult; permissions: Record<string, boolean> }>(`/${app.id}/open`, { tool: entry.tool });
      if (controller.signal.aborted) return;
      const resource = opened.resource.contents.find(c => c.uri === entry.uri && "text" in c);
      if (!resource || !("text" in resource)) throw new Error("App server did not return an HTML resource");
      const metadata = (resource._meta?.ui ?? {}) as { csp?: McpUiResourceCsp; permissions?: McpUiResourcePermissions };
      const granted = Object.fromEntries(Object.entries(metadata.permissions ?? {}).filter(([name]) => opened.permissions[name])) as McpUiResourcePermissions;
      const allow = buildAllowAttribute(granted);
      if (allow) iframe.setAttribute("allow", allow); else iframe.removeAttribute("allow");
      bridge = new AppBridge(null, { name: "Omni Workplace", version: "1.0.0" }, {
        serverTools: {}, serverResources: {}, openLinks: {},
      }, { hostContext: { theme: document.documentElement.classList.contains("dark") ? "dark" : "light", platform: "web", displayMode: "fullscreen", availableDisplayModes: ["fullscreen"], containerDimensions: { height: iframe.clientHeight, width: iframe.clientWidth } } });
      bridgeRef.current = bridge;
      bridge.oncalltool = async params => {
        try { return await extensionRequest<CallToolResult>(`/${app.id}/tools/call`, {name: params.name, arguments: params.arguments ?? {}}); }
        catch (error) { return { isError: true, content: [{ type: "text", text: (error as Error).message }] }; }
      };
      bridge.onreadresource = params => extensionRequest<ReadResourceResult>(`/${app.id}/resources/read?uri=${encodeURIComponent(params.uri)}`);
      bridge.onopenlink = async ({ url }) => {
        const link = new URL(url, window.location.origin);
        if (!["http:", "https:"].includes(link.protocol)) throw new Error("Only HTTP(S) links can be opened");
        if (app.kind === "builtin" && link.origin === window.location.origin && (link.pathname === "/" || link.pathname.startsWith("/github") || link.pathname.startsWith("/linear"))) {
          navigate(link.pathname + link.search);
        } else window.open(link.href, "_blank", "noopener,noreferrer");
        return {};
      };
      bridge.onrequestdisplaymode = async () => ({ mode: "fullscreen" });
      const initialized = new Promise<void>(resolve => { bridge!.oninitialized = () => resolve(); });
      const ready = new Promise<void>((resolve, reject) => {
        const listener = (event: MessageEvent) => {
          if (event.source === iframe.contentWindow && event.origin === isolatedOrigin && event.data?.method === "ui/notifications/sandbox-proxy-ready") {
            clearTimeout(timer); window.removeEventListener("message", listener); resolve();
          }
        };
        window.addEventListener("message", listener);
        disposeReady = () => window.removeEventListener("message", listener);
        timer = setTimeout(() => reject(new Error("App sandbox did not load. Check its HTTPS origin and proxy configuration.")), 20000);
      });
      const url = new URL("/mcp-app-sandbox", isolatedOrigin);
      if (url.origin === window.location.origin) throw new Error("Apps must run on a separate origin");
      url.searchParams.set("csp", JSON.stringify(metadata.csp ?? {}));
      iframe.src = url.href;
      iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
      await ready;
      if (controller.signal.aborted) return;
      await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
      await bridge.sendSandboxResourceReady({ html: resource.text, csp: metadata.csp, permissions: granted });
      await Promise.race([initialized, new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("App initialization timed out")), 20000);
        controller.signal.addEventListener("abort", () => reject(new Error("App closed")), {once:true});
      })]);
      clearTimeout(timer);
      if (controller.signal.aborted) return;
      await bridge.sendToolInput({ arguments: {} });
      await bridge.sendToolResult(opened.result);
      resize = new ResizeObserver(() => {
        if (iframe.clientWidth && iframe.clientHeight) void bridge!.sendHostContextChange({containerDimensions:{width:iframe.clientWidth,height:iframe.clientHeight}});
      });
      resize.observe(iframe);
      theme = new MutationObserver(() => { void bridge!.sendHostContextChange({theme: document.documentElement.classList.contains("dark") ? "dark" : "light"}); });
      theme.observe(document.documentElement, {attributes:true,attributeFilter:["class","data-theme"]});
      if (app.kind === "builtin") {
        await bridge.sendHostContextChange({ _meta: { "workplace/path": route.current } });
        unsubscribe = subscribe(event => {
          if (event.type !== app.id) return;
          void bridge!.sendHostContextChange({ _meta: { "workplace/event": event } });
        });
      }
    };
    void start().catch(error => { if (!controller.signal.aborted) setError(error.message); });
    return () => {
      controller.abort(); clearTimeout(timer); disposeReady?.(); unsubscribe?.(); resize?.disconnect(); theme?.disconnect();
      bridgeRef.current = null;
      if (bridge) void bridge.teardownResource({}).catch(() => {}).finally(() => bridge?.close());
    };
  }, [app.id, app.sandboxOrigin, entry.tool, sandboxOrigin, navigate]);
  useEffect(() => {
    if (app.kind === "builtin") void bridgeRef.current?.sendHostContextChange({ _meta: { "workplace/path": path } });
  }, [path, app.kind]);
  return <div className="relative flex min-h-0 flex-1 flex-col">
    {error && <p role="alert" className="p-6 text-sm text-destructive">{error}</p>}
    <iframe ref={frame} title={entry.name} className="min-h-0 w-full flex-1 border-0" referrerPolicy="no-referrer" />
  </div>;
}
