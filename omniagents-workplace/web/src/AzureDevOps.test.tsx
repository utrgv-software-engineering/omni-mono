import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AzureDevOpsDialog } from "./AzureDevOps";

let root: Root;
let container: HTMLDivElement;
let calls: { method: string; body?: Record<string, unknown> }[];
const connection = { id: "one", revision: 3, label: "School", organization: "school", auth_type: "pat", credential_env: null, credential_configured: true, scopes: [], app_ids: ["factory"], events: ["pull_request_opened"], write_tools: [], push_branches: ["refs/heads/factory/*"], interval_seconds: 60, enabled: false, health: { error: "Check permissions" }, pending_deliveries: 2 };
const inventory = { connections: [connection], apps: [{ id: "factory", name: "Factory" }], events: ["pull_request_opened", "push"], write_tools: ["ado_complete_pull_request", "git_push"] };

beforeEach(async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, body });
    return { ok: true, json: async () => ["PUT", "POST"].includes(method) ? { ...connection, ...body, revision: 4 } : inventory };
  }));
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<AzureDevOpsDialog open onOpenChange={() => {}} />));
});

afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals();
});

const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find(value => value.textContent === name)!;

it("saves credentials independently of draft activation, apps and resources", async () => {
  await act(async () => button("Configure").click());
  const password = document.querySelector('input[type="password"]') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(password, "replacement-token");
    password.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const enable = [...document.querySelectorAll("label")].find(value => value.textContent === "Enable connection")!.querySelector("input")!;
  await act(async () => enable.click());
  await act(async () => button("Save credentials").click());
  const saved = calls.find(value => value.method === "PUT")!.body!;
  expect(saved.token).toBe("replacement-token");
  expect(saved.enabled).toBe(false);
  expect(saved.scopes).toEqual([]);
  expect(document.body.textContent).toContain("Credentials saved");
});

it("preserves unsaved repository selections when credentials are saved", async () => {
  const original = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (String(path).includes("/available?project=")) return { ok: true, json: async () => ({ repositories: [{ id: "repo", name: "Application" }] }) };
    if (String(path).endsWith("/available")) return { ok: true, json: async () => ({ projects: [{ id: "project", name: "Faculty Portal" }] }) };
    return original(path, init);
  }));
  await act(async () => button("Configure").click());
  await act(async () => button("Load projects").click());
  const choose = async (label: string) => {
    const input = [...document.querySelectorAll("label")].find(value => value.textContent === label)!.querySelector("input")!;
    await act(async () => input.click());
    return input;
  };
  await choose("Faculty Portal");
  await act(async () => button("Load repositories").click());
  await choose("Repository · Application");
  await act(async () => button("Save credentials").click());
  expect(calls.find(value => value.method === "PUT")!.body!.scopes).toEqual([]);
  const selected = [...document.querySelectorAll("label")].find(value => value.textContent === "Repository · Application")!.querySelector("input")!;
  expect(selected.checked).toBe(true);
  await act(async () => button("Save connection").click());
  expect(calls.filter(value => value.method === "PUT").at(-1)!.body!.scopes).toEqual([{ project: "project", repositories: ["repo"], pipelines: [], boards: false }]);
});

it("shows disabled state, queue and sanitized polling health", () => {
  expect(document.body.textContent).toContain("Disabled · 2 queued");
  expect(document.body.textContent).toContain("Check permissions");
});

it("retains encrypted credential without submitting public metadata", async () => {
  await act(async () => button("Configure").click());
  expect((document.querySelector('input[type="password"]') as HTMLInputElement).value).toBe("");
  await act(async () => button("Save connection").click());
  const saved = calls.find(value => value.method === "PUT")!.body!;
  expect(saved.revision).toBe(3);
  expect(saved.enabled).toBe(false);
  expect(saved.write_tools).toEqual([]);
  expect(saved).not.toHaveProperty("token");
  expect(saved).not.toHaveProperty("health");
  expect(saved).not.toHaveProperty("credential_configured");
});

it("keeps revision conflicts visible and offers reload", async () => {
  await act(async () => button("Configure").click());
  const original = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => init?.method === "PUT" ? { ok: false, json: async () => ({ detail: "Connection changed; reload before saving" }) } : original(path, init)));
  await act(async () => button("Save connection").click());
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("reload before saving");
  expect(button("Reload connection")).toBeTruthy();
});

it("new connections start disabled and have no write capabilities", async () => {
  await act(async () => button("Add connection").click());
  const enabled = [...document.querySelectorAll("label")].find(value => value.textContent?.includes("Enable connection"))!.querySelector("input")!;
  expect(enabled.checked).toBe(false);
  expect(document.body.textContent).toContain("Tools are read-only");
});

const openAdvanced = async () => {
  const details = [...document.querySelectorAll("details")].find(value => value.querySelector("summary")?.textContent === "Advanced")!;
  await act(async () => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  return details;
};

it("shows organization and credential with optional settings collapsed and no event picker", async () => {
  await act(async () => button("Configure").click());
  expect(document.querySelector('[aria-label="Organization and credential"]')).toBeTruthy();
  expect(document.querySelector('[aria-label="Projects and repositories"]')).toBeTruthy();
  expect(document.querySelector("select")).toBeNull();
  expect(document.body.textContent).not.toContain("Poll interval");
  expect(document.body.textContent).not.toContain("Authorized apps");
  expect(document.body.textContent).not.toContain("Polling events");
  expect(document.body.textContent).toContain("Set event triggers in Automations");
  expect((await openAdvanced()).open).toBe(true);
  expect(document.body.textContent).toContain("Authorized apps");
  expect(document.body.textContent).toContain("Write capabilities (0)");
  expect(document.body.textContent).toContain("Poll interval");
  expect(document.body.textContent).not.toContain("Polling events");
});

it("collects all supported events on save instead of keeping a hidden trigger filter", async () => {
  await act(async () => button("Configure").click());
  await act(async () => button("Save connection").click());
  expect(calls.find(value => value.method === "PUT")!.body!.events).toEqual(inventory.events);
});

it("uses the organization as the default name and selects the sole Factory app without granting writes", async () => {
  await act(async () => button("Add connection").click());
  const organization = document.querySelector('[aria-label="Organization and credential"] input') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(organization, "school");
    organization.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  const saved = calls.find(value => value.method === "POST")!.body!;
  expect(saved.label).toBe("school");
  expect(saved.app_ids).toEqual(["factory"]);
  expect(saved.write_tools).toEqual([]);
  expect(saved.enabled).toBe(false);
});

it("preserves advanced permissions and resource selections when saving the basic form", async () => {
  const existing = { ...connection, auth_type: "bearer_env", credential_env: "ADO_TOKEN", interval_seconds: 120, write_tools: ["git_push"], push_branches: ["refs/heads/release/*"], scopes: [{ project: "project", repositories: ["repo"], boards: true, pipelines: [7] }] };
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, body });
    return { ok: true, json: async () => method === "PUT" ? { ...existing, ...body } : { ...inventory, connections: [existing] } };
  }));
  await act(async () => root.render(<AzureDevOpsDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<AzureDevOpsDialog open onOpenChange={() => {}} />));
  await act(async () => button("Configure").click());
  expect(document.body.textContent).toContain("Using host authentication");
  await act(async () => button("Save connection").click());
  const saved = calls.find(value => value.method === "PUT")!.body!;
  for (const key of ["auth_type", "credential_env", "interval_seconds", "write_tools", "push_branches", "scopes", "app_ids"] as const) expect(saved[key]).toEqual(existing[key]);
});

it("keeps project and repository selection in the basic form and pipelines in Advanced", async () => {
  const original = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (String(path).includes("/available?project=")) return { ok: true, json: async () => ({ repositories: [{ id: "repo", name: "Application" }], pipelines: [{ id: 7, name: "Deploy" }] }) };
    if (String(path).endsWith("/available")) return { ok: true, json: async () => ({ projects: [{ id: "project", name: "Faculty Portal" }] }) };
    if (String(path).endsWith("/preview")) return { ok: true, json: async () => ({ resources: [{ pull_requests: [{}] }] }) };
    return original(path, init);
  }));
  await act(async () => button("Configure").click());
  await act(async () => button("Load projects").click());
  const choose = async (label: string) => {
    const input = [...document.querySelectorAll("label")].find(value => value.textContent === label)!.querySelector("input")!;
    await act(async () => input.click());
  };
  await choose("Faculty Portal");
  await act(async () => button("Load repositories").click());
  expect(document.body.textContent).toContain("Faculty Portal");
  expect(document.body.textContent).not.toContain("Pipeline · Deploy");
  await choose("Repository · Application");
  await openAdvanced();
  await act(async () => button("Load pipelines").click());
  await choose("Pipeline · Deploy");
  await act(async () => button("Preview").click());
  expect(document.body.textContent).toContain("Read-only preview: 1 active pull requests");
  await act(async () => button("Save connection").click());
  expect(calls.find(value => value.method === "PUT")!.body!.scopes).toEqual([{ project: "project", repositories: ["repo"], boards: false, pipelines: [7] }]);
});

it("does not authorize every app when multiple apps are available", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ ...inventory, apps: [...inventory.apps, { id: "other", name: "Other" }] }) })));
  await act(async () => root.render(<AzureDevOpsDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<AzureDevOpsDialog open onOpenChange={() => {}} />));
  await act(async () => button("Add connection").click());
  expect(document.body.textContent).toContain("Select an authorized app in Advanced");
  await openAdvanced();
  const choices = [...document.querySelectorAll("label")].filter(value => ["Factory", "Other"].includes(value.textContent ?? ""));
  expect(choices.every(value => !value.querySelector("input")!.checked)).toBe(true);
});
