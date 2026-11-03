import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { App as McpApp } from "@modelcontextprotocol/ext-apps";
import { applyTheme } from "@omniagents/ui/themes";
import { Button } from "@omniagents/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@omniagents/ui/sheet";
import { Menu } from "lucide-react";
import { NavSlot, useAdoptSlot } from "../mobile-nav";
import { Toaster } from "@omniagents/ui/sonner";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource-variable/plus-jakarta-sans/wght-italic.css";
import "../index.css";
import Workplace from "../App";
import { GitHubApp, parseGitHubPath } from "../GitHub";
import { LinearApp, parseLinearPath } from "../Linear";
import { type Person, type StreamEvent } from "../api";
import { setAppTransport, type AppTransport } from "./transport";
import { Recorder } from "./Recorder";

export const bridge = new McpApp({ name: "Workplace apps", version: "1.0.0" }, {});
export async function appCall<T>(name: string, args: Record<string, unknown>): Promise<T> {
  const value = await bridge.callServerTool({ name, arguments: args });
  if (value.isError) throw new Error(value.content.filter(c => c.type === "text").map(c => c.text).join("\n") || "App operation failed");
  return value.structuredContent as T;
}
export function base64(bytes: Uint8Array): string {
  let text = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) text += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  return btoa(text);
}
interface Initial { app: string; account: Person; factory: string; origin: string; path: string; selectedRepositories?: string[] }
const listeners = new Set<(event: StreamEvent) => void>();
const routes = new Set<(path: string) => void>();
let transport: AppTransport;
let initialPath: string | undefined;
bridge.onhostcontextchanged = context => {
  if (context.theme) applyTheme(context.theme === "dark" ? "omni" : "teams-light");
  const metadata = context._meta as Record<string, unknown> | undefined;
  const event = metadata?.["workplace/event"] as StreamEvent | undefined;
  if (event) listeners.forEach(listener => listener(event));
  const path = metadata?.["workplace/path"];
  if (typeof path === "string") {
    initialPath = path;
    if (transport) transport.path = path;
    routes.forEach(listener => listener(path));
    window.dispatchEvent(new PopStateEvent("popstate"));
  }
};


function Surface({ value }: { value: Initial }) {
  const [path, setPath] = useState(initialPath || value.path);
  const [version, setVersion] = useState(0);
  const [navigation, setNavigation] = useState(false);
  const [slot] = useState(() => document.createElement("div"));
  const adoptSlot = useAdoptSlot(slot);
  useEffect(() => {
    const update = () => setVersion(v => v + 1);
    routes.add(setPath); listeners.add(update);
    return () => { routes.delete(setPath); listeners.delete(update); };
  }, []);
  const navigate = (path: string) => { setNavigation(false); transport.navigate(path); };
  return <NavSlot.Provider value={slot}><div className="flex h-dvh min-h-0 flex-col bg-background text-foreground">
    <Toaster />
    {value.app === "linear" && <div className="p-2 md:hidden"><Button variant="ghost" size="icon" aria-label="Open Linear navigation" onClick={()=>setNavigation(true)}><Menu /></Button></div>}
    <Sheet open={navigation} onOpenChange={setNavigation}><SheetContent side="left"><SheetTitle className="sr-only">App navigation</SheetTitle><div ref={adoptSlot} /></SheetContent></Sheet>
    {value.app === "slack" ? <Workplace embedded account={value.account} environment="production" configurable={false} factory={false} onSignOut={() => {}} /> :
     value.app === "github" ? <GitHubApp route={parseGitHubPath(path) || { view: "repos" }} selectedRepositories={value.selectedRepositories ?? []} actor={value.account.github_login} admin={value.account.admin} version={version} navigate={navigate} /> :
     value.app === "linear" ? <LinearApp route={parseLinearPath(path) || parseLinearPath("/linear")!} me={value.account.linear_id} version={version} navigate={navigate} /> :
     value.app === "recorder" ? <Recorder account={value.account} /> : null}
  </div></NavSlot.Provider>;
}

bridge.ontoolresult = result => {
  const value = result.structuredContent as unknown as Initial;
  if (!value?.app || transport) return;
  transport = {
    path: initialPath || value.path, factory: value.factory, origin: value.origin,
    navigate: path => { void bridge.openLink({ url: value.origin + path }); },
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    request: async (input, init) => {
      const request = new Request("https://request.invalid", { ...init, method: init?.method ?? "GET" });
      const data = new Uint8Array(await request.arrayBuffer());
      const response = await appCall<{ status: number; contentType: string; body: string }>(value.app + ".request", {
        path: input, method: request.method, body: base64(data), encoding: "base64", contentType: request.headers.get("content-type") || "application/json",
      });
      const bytes = Uint8Array.from(atob(response.body), character => character.charCodeAt(0));
      return new Response(response.status === 204 ? null : bytes, { status: response.status, headers: { "Content-Type": response.contentType } });
    },
  };
  setAppTransport(transport);
  applyTheme(bridge.getHostContext()?.theme === "light" ? "teams-light" : "omni");
  document.addEventListener("click", event => {
    const anchor = (event.target as Element)?.closest("a");
    if (!anchor || anchor.hasAttribute("download")) return;
    const href = anchor.getAttribute("href");
    if (!href || href.startsWith("#")) return;
    event.preventDefault();
    void bridge.openLink({ url: new URL(href, value.origin + value.factory + "/").href });
  });
  createRoot(document.getElementById("root")!).render(<Surface value={value} />);
};
void bridge.connect().catch(error => { document.getElementById("root")!.textContent = error.message; });
