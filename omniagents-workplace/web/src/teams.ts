import { createNestablePublicClientApplication, InteractionRequiredAuthError, type IPublicClientApplication, type AccountInfo } from "@azure/msal-browser";
import type { app as TeamsApp } from "@microsoft/teams-js";
import { setHostTicketProvider } from "@omniagents/client/rpc/ws-ticket";

const prefix = "/workplace/auth/teams";
let nativeFetch: typeof fetch;
let app: typeof TeamsApp;
let client: IPublicClientApplication;
let account: AccountInfo | null = null;
let scope = "";
let session = "";
let accessToken = "";
let expiresAt = 0;
let pending: Promise<void> | undefined;
let active = false;
let initialized = false;
let loginHint = "";
let tenantId = "";
export const inTeams = () => active;
export const teamsRequested = () => {
  const requested = new URLSearchParams(window.location.search).get("host") === "teams";
  try {
    if (requested) sessionStorage.setItem("workplace-teams-host", "true");
    return requested || (window.parent !== window && sessionStorage.getItem("workplace-teams-host") === "true");
  } catch {
    return requested;
  }
};
export const protectedFile = (path: string) => /^\/(?:slack\/files\/|linear\/uploads\/[^/]+$|workplace\/api\/shared\/(?:content|download-folder)(?:\?|$))/.test(path);

async function checked(response: Response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Workplace authentication failed (${response.status})`);
  }
  if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Teams sign-in is not enabled for this Workplace deployment.");
  return response.json();
}

async function renew(interactive = false) {
  const context = await app.getContext();
  const hostAccountId = context.user?.id;
  loginHint = context.user?.loginHint ?? loginHint;
  if (hostAccountId && account?.localAccountId !== hostAccountId) account = client.getAccount({ tenantId, localAccountId: hostAccountId });
  const request = { scopes: [scope], account: account ?? undefined, loginHint: loginHint || undefined };
  const result = interactive ? await client.acquireTokenPopup(request) : await client.acquireTokenSilent(request);
  if (!result.account || result.account.tenantId !== tenantId) throw new Error("Select your organization's Teams account.");
  if (hostAccountId && result.account.localAccountId !== hostAccountId) throw new Error("Sign in with the same account you are using in Teams.");
  account = result.account;
  accessToken = result.accessToken;
  const body = await checked(await nativeFetch(prefix + "/session", {
    method: "POST", credentials: "omit", redirect: "error",
    headers: { Authorization: `Bearer ${accessToken}`, "X-Workplace-Session": session },
  }));
  session = body.session;
  expiresAt = body.expiresAt * 1000;
}

async function ready() {
  if (Date.now() < expiresAt - 60000) return;
  pending ??= renew().catch(error => {
    window.dispatchEvent(new CustomEvent("workplace-teams-auth-error", { detail: authMessage(error) }));
    throw error;
  }).finally(() => { pending = undefined; });
  await pending;
}

export function authMessage(error: unknown) {
  if (error instanceof InteractionRequiredAuthError) return "Microsoft needs you to continue sign-in. Your organization may require consent or additional verification.";
  if (error instanceof Error && /No Parent window|Initialization Failed/i.test(error.message)) return "Open this tab from Microsoft Teams, or use Workplace in your browser.";
  return error instanceof Error ? error.message : "Microsoft sign-in could not complete.";
}

export async function teamsFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = new Request(input instanceof Request ? input : new URL(String(input), location.href), init);
  const url = new URL(request.url);
  if (!active || url.origin !== location.origin || !(url.pathname.startsWith("/workplace/api/") || protectedFile(url.pathname))) return nativeFetch(input, init);
  await ready();
  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);
  headers.set("X-Workplace-Session", session);
  const response = await nativeFetch(new Request(request, { headers, credentials: "omit", redirect: "error" }));
  if (response.status === 401) {
    expiresAt = 0;
    window.dispatchEvent(new CustomEvent("workplace-teams-auth-error", { detail: "Your Workplace session ended. Continue with Microsoft to sign in again." }));
  }
  return response;
}

export async function ticketUrl(value: string, signal?: AbortSignal): Promise<string> {
  if (!active) return value;
  const url = new URL(value, location.href);
  const httpOrigin = url.origin.replace(/^ws/, "http");
  if (httpOrigin !== location.origin) throw new Error("Cannot authorize a different origin.");
  await ready();
  const body = await checked(await nativeFetch(prefix + "/ticket", {
    method: "POST", credentials: "omit", redirect: "error", signal,
    headers: { Authorization: `Bearer ${accessToken}`, "X-Workplace-Session": session, "Content-Type": "application/json" },
    body: JSON.stringify({ path: url.pathname + url.search }),
  }));
  url.searchParams.set("workplace_ticket", body.ticket);
  return url.href;
}

export async function initializeTeams(interactive = false) {
  if (!initialized) {
    nativeFetch = window.fetch.bind(window);
    const teams = await import("@microsoft/teams-js");
    app = teams.app;
    await app.initialize();
    if (!teams.nestedAppAuth.isNAAChannelRecommended()) throw new Error("This Teams client does not support nested sign-in. Update Teams or open Workplace in your browser.");
    const config = await checked(await nativeFetch(prefix + "/config", { credentials: "omit" }));
    scope = config.scope;
    tenantId = config.tenantId;
    const context = await app.getContext();
    loginHint = context.user?.loginHint ?? "";
    client = await createNestablePublicClientApplication({
      auth: { clientId: config.clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: `brk-multihub://${location.host}` },
      cache: { cacheLocation: "memoryStorage" },
    });
    account = client.getAccount({ tenantId, ...(loginHint ? { loginHint } : {}) });
    initialized = true;
  }
  await renew(interactive);
  if (!active) {
    active = true;
    window.fetch = teamsFetch;
    setHostTicketProvider(ticketUrl);
    document.addEventListener("click", event => {
      const anchor = (event.target as Element)?.closest?.("a");
      if (!anchor || anchor.hasAttribute("data-teams-ticket")) return;
      const url = new URL(anchor.href, location.href);
      if (url.origin !== location.origin || !protectedFile(url.pathname)) return;
      event.preventDefault();
      void ticketUrl(url.href).then(destination => {
        const download = document.createElement("a");
        download.href = destination;
        download.rel = "noreferrer";
        download.target = anchor.target;
        download.setAttribute("data-teams-ticket", "true");
        if (anchor.download) download.download = anchor.download;
        download.click();
      }).catch(error => window.dispatchEvent(new CustomEvent("workplace-teams-auth-error", { detail: authMessage(error) })));
    });
  }
}

export function teamsSignOut() {
  session = "";
  accessToken = "";
  expiresAt = 0;
  window.dispatchEvent(new CustomEvent("workplace-teams-auth-error", { detail: "You signed out of Workplace. Select Continue with Microsoft to sign in again." }));
}

export function teamsSubscribe(onEvent: (event: { type: string; data?: unknown }) => void) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const connect = async () => {
    try {
      const response = await fetch("/workplace/api/stream", { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error("Live updates unavailable");
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const result = await reader.read();
        if (result.done) break;
        buffer += result.value;
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const lines = block.split("\n");
          const type = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
          const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trim()).join("\n");
          if (type && ["ready", "slack", "github", "linear", "canvas", "wire"].includes(type)) onEvent({ type, data: JSON.parse(data || "{}") });
        }
      }
    } catch {
      if (controller.signal.aborted) return;
    }
    if (!controller.signal.aborted) timer = setTimeout(() => void connect(), 3000);
  };
  void connect();
  return () => { controller.abort(); clearTimeout(timer); };
}
