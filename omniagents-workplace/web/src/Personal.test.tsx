// @vitest-environment jsdom
import { act, useEffect, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatHost } from "@omniagents/chat-ui/host";
import type { Person } from "./api";
import { PersonalApp } from "./Personal";

const { app, config, unmounted, hosts } = vi.hoisted(() => ({
  hosts: [] as ChatHost[],
  app: vi.fn(),
  config: vi.fn(),
  unmounted: vi.fn(),
}));
vi.mock("./ChatViewer", () => ({ ChatViewer: ({ value, onClose }: { value: {kind:string;title:string}; onClose:()=>void }) => <section data-viewer={value.kind}>{value.title}<button onClick={onClose}>Close viewer</button></section> }));
vi.mock("./Desktop", () => ({ useDesktopAvailable: () => false, Desktop: () => <div>Desktop</div> }));
vi.mock("@omniagents/chat-ui/components/omni-avatar/AgentAvatar", () => ({
  AgentAvatar: ({ seed, mood }: { seed: string; mood?: string }) => <span data-avatar={seed} data-mood={mood ?? "idle"} />,
}));
vi.mock("./PublishedChat", () => ({
  PublishedChat: (props: { sessionId: string }) => {
    useEffect(() => () => unmounted(props.sessionId), [props.sessionId]);
    app(props);
    return <div>Shared chat</div>;
  },
}));
vi.mock("@omniagents/chat-ui/host", () => ({
  ChatHostProvider: ({ host, children }: { host: ChatHost; children: ReactNode }) => { hosts.push(host); return children; },
}));
vi.mock("@omniagents/chat-ui/rpc-context", () => ({
  RPCClientProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@omniagents/chat-ui/ui-config", () => ({
  UiConfigProvider: ({
    children,
    connection,
  }: {
    children: ReactNode;
    connection: unknown;
  }) => {
    config(connection);
    return children;
  },
}));
vi.mock("@omniagents/ui/tooltip", () => ({
  TooltipProvider: ({ children }: { children: ReactNode }) => children,
}));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let root: Root;
let container: HTMLDivElement;
let wide = false;
let chats: {
  id: string;
  title: string;
  archived: boolean;
  kind: "main" | "side" | "automation";
  agent: string;
  automation?: string;
  revision?: string;
  unread?: boolean;
  waiting?: boolean;
  running?: boolean;
  working_for?: string;
  last_message?: { role: "user" | "assistant"; author_name?: string; text: string; at: number };
}[];
const threads = async () => act(async () => button("Threads with Coordinator").click());
const button = (name: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(
    (b) => b.getAttribute("aria-label") === name || b.textContent === name,
  )!;
beforeEach(() => {
  vi.clearAllMocks();
  hosts.length = 0;
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  window.history.replaceState(null, "", "/personal");
  wide = false;
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: wide,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  chats = [
    { id: "factory-personal", title: "Main Chat", archived: false, kind: "main", agent: "coordinator" },
    { id: "personal-side", title: "Planning", archived: false, kind: "side", agent: "coordinator" },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options) => {
      if (String(url).endsWith("/seen")) return { ok: true, json: async () => ({ ok: true }) };
      if (options?.method === "POST") {
        const created = {
          id: "personal-new",
          title: "New chat",
          archived: false,
          kind: "side" as const,
          agent: JSON.parse(options.body).agent,
        };
        chats.push(created);
        return { ok: true, json: async () => created };
      }
      return { ok: true, json: async () => ({ chats, agents: ["coordinator"], chat_agent: "coordinator" }) };
    }),
  );
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});
it("embeds the public chat with a registered session and retained viewport", async () => {
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  expect(app).toHaveBeenCalledWith({
    sessionId: "factory-personal",
    showAvatar: true,
    onPresent: expect.any(Function),
    active: true,
    agentId: "coordinator",
    agentName: "Coordinator",
  });
  expect(config).toHaveBeenCalledWith({
    baseUrl: `${location.origin}/workplace/api/personal/chats/factory-personal/runtime/?minimal=true`,
    realtime: true,
  });
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not connect on authorization failure and can retry", async () => {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: false,
    json: async () => ({ detail: "Admin access required" }),
  } as Response);
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  expect(app).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Admin access required");
  await act(async () => button("Retry").click());
  expect(app).toHaveBeenCalled();
});
it("creates a side chat immediately without a naming dialog and closes mobile navigation", async () => {
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  await threads();
  expect(document.body.textContent).not.toContain("Archived");
  await act(async () => button("New side chat").click());
  expect(fetch).toHaveBeenCalledWith(
    "/workplace/api/personal/chats",
    expect.objectContaining({ method: "POST", body: JSON.stringify({agent: "coordinator"}) }),
  );
  expect(document.querySelector('[aria-label="Threads"]')).toBeNull();
  expect(location.search).toBe("?chat=personal-new");
  expect(config).toHaveBeenLastCalledWith({
    baseUrl: `${location.origin}/workplace/api/personal/chats/personal-new/runtime/?minimal=true`,
    realtime: true,
  });
});
it("keeps desktop navigation open when switching and remembers the selected chat", async () => {
  wide = true;
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  await act(async () => button("Show chats").click());
  await threads();
  await act(async () => button("Planning").click());
  expect(
    container.querySelector('aside[aria-label="Conversations"]'),
  ).not.toBeNull();
  expect(JSON.parse(localStorage.getItem("workplace.chat.selected")!)).toBe(
    "personal-side",
  );
  await threads();
  await act(async () => button("Planning").click());
  expect(container.textContent).toContain("Shared chat");
  expect(unmounted).not.toHaveBeenCalled();
});
it("prefers a direct link over the remembered selection and falls back from a deleted chat", async () => {
  localStorage.setItem(
    "workplace.chat.selected",
    JSON.stringify("personal-side"),
  );
  window.history.replaceState(null, "", "/personal?chat=factory-personal");
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  expect(app).toHaveBeenCalledWith(
    expect.objectContaining({ sessionId: "factory-personal", active: true }),
  );
  await act(async () => root.unmount());
  root = createRoot(container);
  window.history.replaceState(null, "", "/personal?chat=deleted");
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  expect(app).toHaveBeenCalledWith(
    expect.objectContaining({ sessionId: "factory-personal", active: true }),
  );
});
it("restores the remembered chat without a URL selection", async () => {
  localStorage.setItem(
    "workplace.chat.selected",
    JSON.stringify("personal-side"),
  );
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  expect(app).toHaveBeenCalledWith(
    expect.objectContaining({ sessionId: "personal-side", active: true }),
  );
});
it("shows unread and attention indicators and marks a chat read on the server", async () => {
  chats[1].revision = "new-result";
  chats[1].unread = true;
  chats.push({
    id: "archived",
    title: "Old chat",
    archived: true,
    kind: "side",
    agent: "coordinator",
    waiting: true,
  });
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  await act(async () => button("Show chats").click());
  // The agent row carries its chats' unread activity.
  expect(
    document.querySelector('[aria-label="Coordinator"] [aria-label="Unread activity"]'),
  ).not.toBeNull();
  await threads();
  expect(document.body.textContent).toContain("Archived · 1");
  await act(async () => button("Planning").click());
  expect(fetch).toHaveBeenCalledWith(
    "/workplace/api/personal/chats/personal-side/seen",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ revision: "new-result" }) }),
  );
});


it("replaces the single viewer, closes it, and scopes it to its chat", async () => {
  wide = true;
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} />));
  const host = hosts[0];
  const common = { title: "Plan", id: "abc", presentation_id: "one", issued_at: Date.now() };
  await act(async () => host.presentView?.({ ...common, kind: "artifact" }));
  expect(app).toHaveBeenLastCalledWith(expect.objectContaining({showAvatar:false}));
  expect(document.querySelectorAll("[data-viewer]")).toHaveLength(1);
  expect(document.querySelector("[data-viewer]")?.getAttribute("data-viewer")).toBe("artifact");
  await act(async () => host.presentView?.({ ...common, kind: "desktop", title: "Desktop" }));
  expect(document.querySelectorAll("[data-viewer]")).toHaveLength(1);
  expect(document.querySelector("[data-viewer]")?.getAttribute("data-viewer")).toBe("desktop");
  await threads();
  await act(async () => button("Planning").click());
  expect(document.querySelector("[data-viewer]")).toBeNull();
  await threads();
  await act(async () => button("Main Chat").click());
  expect(document.querySelector("[data-viewer]")?.getAttribute("data-viewer")).toBe("desktop");
  await act(async () => button("Close viewer").click());
  expect(app).toHaveBeenLastCalledWith(expect.objectContaining({showAvatar:true}));
  expect(document.querySelector("[data-viewer]")).toBeNull();
});

it("lists each agent once, with its face, latest message and time, default first then by activity", async () => {
  const now = Date.now() / 1000;
  chats[0].last_message = { role: "assistant", text: "booked the venue", at: now - 86400 * 3 };
  chats.push(
    { id: "main-reviewer", title: "Main Chat", archived: false, kind: "main", agent: "reviewer", last_message: { role: "user", author_name: "Priya", text: "review   PR 12", at: now - 60 } },
    { id: "auto-1", title: "PR #12 · Add pagination", archived: false, kind: "automation", agent: "reviewer", automation: "pr-opened", running: true, working_for: "PR #12 · Add pagination" },
    { id: "main-designer", title: "Main Chat", archived: false, kind: "main", agent: "designer", last_message: { role: "assistant", text: "draft ready", at: now - 3600 } },
  );
  vi.mocked(fetch).mockResolvedValue({ok:true,json:async()=>({chats,agents:["coordinator","designer","reviewer"],chat_agent:"coordinator",agent_details:[{id:"coordinator",name:"Alex"},{id:"reviewer",name:"Code Reviewer"}]})} as Response);
  await act(async () => root.render(<PersonalApp account={{id:"admin",name:"Priya Shah",admin:true} as Person}/>));
  expect(app).toHaveBeenCalledWith(expect.objectContaining({sessionId:"factory-personal",agentName:"Alex",agentId:"coordinator"}));
  await act(async () => button("Show chats").click());
  const nav = document.querySelector('nav[aria-label="Agents"]')!;
  const rows = [...nav.querySelectorAll("button")];
  // The default agent stays first; the rest follow their latest activity.
  expect(rows.map(r => r.getAttribute("aria-label"))).toEqual(["Alex", "Code Reviewer", "Designer"]);
  expect(rows[0].textContent).toContain("booked the venue");
  expect(rows[2].textContent).toContain("draft ready");
  // Working on an automation shows on the face and in the preview.
  expect(rows[1].querySelector("[data-mood]")!.getAttribute("data-mood")).toBe("thinking");
  expect(rows[1].textContent).toContain("Working for PR #12");
  expect(document.body.textContent).toContain("PS");
  await act(async () => rows[1].click());
  expect(app).toHaveBeenLastCalledWith(expect.objectContaining({sessionId:"main-reviewer",agentName:"Code Reviewer",agentId:"reviewer"}));
  await act(async () => button("Threads with Code Reviewer").click());
  expect(document.body.textContent).toContain("Automations · 1");
  await act(async () => button("New side chat").click());
  expect(fetch).toHaveBeenCalledWith("/workplace/api/personal/chats",expect.objectContaining({method:"POST",body:JSON.stringify({agent:"reviewer"})}));
});

it("searches agents by name and latest message", async () => {
  chats[0].last_message = { role: "assistant", text: "venue booked", at: 1 };
  vi.mocked(fetch).mockResolvedValue({ok:true,json:async()=>({chats:[...chats,{ id: "main-designer", title: "Main Chat", archived: false, kind: "main", agent: "designer" }],agents:["coordinator","designer"],chat_agent:"coordinator"})} as Response);
  await act(async () => root.render(<PersonalApp account={{id:"admin",admin:true} as Person}/>));
  await act(async () => button("Show chats").click());
  const search = document.querySelector<HTMLInputElement>('input[aria-label="Search agents"]')!;
  const type = async (value: string) => act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(search, value);
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await type("venue");
  expect([...document.querySelectorAll('nav[aria-label="Agents"] button')].map(b => b.getAttribute("aria-label"))).toEqual(["Coordinator"]);
  await type("zzz");
  expect(document.body.textContent).toContain("No agents match.");
});

it("never offers to manage a Main Chat and names who a chat is working for", async () => {
  chats[0].running = true;
  chats[0].working_for = "Priya";
  await act(async()=>root.render(<PersonalApp account={{id:"admin",admin:true} as Person}/>));
  // One header: the toggle, the agent as its heading, then the actions.
  const header = container.querySelector("header")!;
  expect(header.querySelector("h2")!.textContent).toBe("Coordinator");
  expect(header.textContent).toContain("Working for Priya");
  expect([...header.querySelectorAll("button")].map(b => b.getAttribute("aria-label"))).toEqual(
    ["Show chats", "Threads with Coordinator", "Readiness of Coordinator"],
  );
  await threads();
  expect(button("Manage Main Chat")).toBeUndefined();
  expect(button("Manage Planning")).toBeDefined();
});

it("shows an empty state without connecting when there are no chats", async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ok:true,json:async()=>({chats:[],agents:[],chat_agent:"coordinator"})} as Response);
  await act(async()=>root.render(<PersonalApp account={{id:"admin",admin:true} as Person}/>));
  expect(app).not.toHaveBeenCalled();
  expect(container.textContent).toContain("No chats yet.");
});

it("adds an agent from Chat's agent list and opens its Main Chat", async () => {
  wide = true;
  const base = vi.mocked(fetch).getMockImplementation()!;
  let agents = ["coordinator"];
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    const path = String(url);
    if (path.endsWith("/factory/definition"))
      return { ok: true, json: async () => ({ bases: [{ key: "base", name: "Base" }], main: "a".repeat(40) }) } as Response;
    if (path.endsWith("/definition/agent")) {
      agents = ["coordinator", "designer"];
      chats.push({ id: "main-designer", title: "Main Chat", archived: false, kind: "main", agent: "designer" });
      return { ok: true, json: async () => ({ commit: "b".repeat(40), published: true, agent: "designer" }) } as Response;
    }
    if (!options?.method || options.method === "GET")
      return { ok: true, json: async () => ({ chats, agents, chat_agent: "coordinator" }) } as Response;
    return base(url, options);
  });
  await act(async () => root.render(<PersonalApp account={{ id: "admin", admin: true } as Person} navigate={vi.fn()} />));
  await act(async () => {});
  if (!button("Add agent")) await act(async () => button("Show chats").click());
  await act(async () => button("Add agent").click());
  await act(async () => {});
  const type = async (selector: string, value: string) =>
    act(async () => {
      const element = document.querySelector<HTMLInputElement>(selector)!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
  await type("#agent-name", "designer");
  await type("#agent-description", "Designs.");
  await act(async () => [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(b => b.textContent === "Add agent")!.click());
  await act(async () => {});
  await act(async () => {});
  const add = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/definition/agent"))!;
  expect(JSON.parse(String(add[1]!.body))).toEqual({ key: "designer", description: "Designs.", template: "base", chat: true });
  expect(button("Designer").getAttribute("aria-current")).toBe("true");
});
