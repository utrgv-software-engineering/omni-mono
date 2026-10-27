// @vitest-environment jsdom
import { act, useEffect, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PersonalApp } from "./Personal";

const { app, config, unmounted } = vi.hoisted(() => ({
  app: vi.fn(),
  config: vi.fn(),
  unmounted: vi.fn(),
}));
vi.mock("@omniagents/chat-ui/App", () => ({
  App: (props: { sessionId: string }) => {
    useEffect(() => () => unmounted(props.sessionId), [props.sessionId]);
    app(props);
    return <div>Shared chat</div>;
  },
}));
vi.mock("@omniagents/chat-ui/host", () => ({
  ChatHostProvider: ({ children }: { children: ReactNode }) => children,
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
  revision?: string;
  waiting?: boolean;
  running?: boolean;
}[];
const button = (name: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(
    (b) => b.getAttribute("aria-label") === name || b.textContent === name,
  )!;
beforeEach(() => {
  vi.clearAllMocks();
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
    { id: "factory-personal", title: "Main Chat", archived: false },
    { id: "personal-side", title: "Planning", archived: false },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      if (options?.method === "POST") {
        const created = {
          id: "personal-new",
          title: "New chat",
          archived: false,
        };
        chats.push(created);
        return { ok: true, json: async () => created };
      }
      return { ok: true, json: async () => ({ chats }) };
    }),
  );
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});
it("embeds the shared chat with a registered session and retained viewport", async () => {
  await act(async () => root.render(<PersonalApp />));
  expect(app).toHaveBeenCalledWith({
    sessionId: "factory-personal",
    omniAvatar: true,
    defaultVoiceCaptions: false,
    active: true,
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
  await act(async () => root.render(<PersonalApp />));
  expect(app).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Admin access required");
  await act(async () => button("Retry").click());
  expect(app).toHaveBeenCalled();
});
it("creates a side chat immediately without a naming dialog and closes mobile navigation", async () => {
  await act(async () => root.render(<PersonalApp />));
  await act(async () => button("Show chats").click());
  expect(document.body.textContent).not.toContain("Conversations with Omni");
  expect(document.body.textContent).not.toContain("Archived");
  await act(async () => button("New side chat").click());
  expect(fetch).toHaveBeenCalledWith(
    "/workplace/api/personal/chats",
    expect.objectContaining({ method: "POST", body: "{}" }),
  );
  expect(document.querySelector('[aria-label="Chat sessions"]')).toBeNull();
  expect(location.search).toBe("?chat=personal-new");
  expect(config).toHaveBeenLastCalledWith({
    baseUrl: `${location.origin}/workplace/api/personal/chats/personal-new/runtime/?minimal=true`,
    realtime: true,
  });
});
it("keeps desktop navigation open when switching and remembers the selected chat", async () => {
  wide = true;
  await act(async () => root.render(<PersonalApp />));
  await act(async () => button("Show chats").click());
  await act(async () => button("Planning").click());
  expect(
    container.querySelector('aside[aria-label="Conversations"]'),
  ).not.toBeNull();
  expect(JSON.parse(localStorage.getItem("workplace.chat.selected")!)).toBe(
    "personal-side",
  );
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
  await act(async () => root.render(<PersonalApp />));
  expect(app).toHaveBeenCalledWith(
    expect.objectContaining({ sessionId: "factory-personal", active: true }),
  );
  await act(async () => root.unmount());
  root = createRoot(container);
  window.history.replaceState(null, "", "/personal?chat=deleted");
  await act(async () => root.render(<PersonalApp />));
  expect(app).toHaveBeenCalledWith(
    expect.objectContaining({ sessionId: "factory-personal", active: true }),
  );
});
it("restores the remembered chat without a URL selection", async () => {
  localStorage.setItem(
    "workplace.chat.selected",
    JSON.stringify("personal-side"),
  );
  await act(async () => root.render(<PersonalApp />));
  expect(app).toHaveBeenCalledWith(
    expect.objectContaining({ sessionId: "personal-side", active: true }),
  );
});
it("shows unread and attention indicators and a conditional archive section", async () => {
  chats[1].revision = "new-result";
  chats.push({
    id: "archived",
    title: "Old chat",
    archived: true,
    waiting: true,
  });
  await act(async () => root.render(<PersonalApp />));
  await act(async () => button("Show chats").click());
  expect(
    document.querySelector('[aria-label="Unread activity"]'),
  ).not.toBeNull();
  expect(document.body.textContent).toContain("Archived · 1");
  await act(async () => button("Planning").click());
  expect(
    JSON.parse(localStorage.getItem("workplace.chat.seen")!)["personal-side"],
  ).toBe("new-result");
});
