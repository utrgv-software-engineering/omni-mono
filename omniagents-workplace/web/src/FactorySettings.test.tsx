import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FactorySettingsDialog } from "./FactorySettings";

let container: HTMLDivElement;
let root: Root;
let calls: { method: string; body: unknown }[];
let settings: Record<string, unknown>;
const saved = vi.fn();

beforeEach(async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  calls = [];
  saved.mockReset();
  settings = { revision: 0, display_name: "Acme Factory", sources: [{ source: "teams_intake", identity: "stable", marker: "@acme-factory" }], applied_revision: 0, status: "applied", error: null };
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, body });
    if (method === "PUT") settings = { ...settings, ...body, revision: body.revision + 1, applied_revision: body.revision + 1 };
    return { ok: true, json: async () => settings };
  }));
  await act(async () => root.render(<FactorySettingsDialog open onOpenChange={() => {}} onSaved={saved} />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find(element => element.textContent === name)!;
const fill = async (id: string, value: string) => {
  await act(async () => {
    const input = document.getElementById(id) as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

it("loads current values and previews the literal Teams marker", () => {
  expect((document.getElementById("factory-display-name") as HTMLInputElement).value).toBe("Acme Factory");
  expect(document.querySelector('[aria-label="Example Teams message"]')?.textContent).toContain("@acme-factory help with this thread");
  expect(button("Save changes").disabled).toBe(true);
  expect(document.body.textContent).toContain("Applied to Factory");
});

it("saves independent name and marker with concurrency revision and refreshes the UI", async () => {
  await fill("factory-display-name", "Research");
  await fill("factory-marker-0", "@research");
  await act(async () => button("Save changes").click());
  expect(calls.find(call => call.method === "PUT")?.body).toEqual({ revision: 0, display_name: "Research", sources: [{ source: "teams_intake", identity: "stable", marker: "@research" }] });
  expect(saved).toHaveBeenCalledOnce();
  expect(button("Save changes").disabled).toBe(true);
});

it("rejects invalid names and markers before saving", async () => {
  await fill("factory-marker-0", "not a marker");
  expect(button("Save changes").disabled).toBe(true);
  await fill("factory-marker-0", "@research");
  await fill("factory-display-name", "   ");
  expect(button("Save changes").disabled).toBe(true);
  expect(calls.every(call => call.method === "GET")).toBe(true);
});

it("keeps edits and offers reload on conflicting saves", async () => {
  const fetch = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => init?.method === "PUT"
    ? { ok: false, json: async () => ({ detail: "Settings changed in another window. Reload before saving." }) }
    : fetch(path, init)));
  await fill("factory-display-name", "Research");
  await act(async () => button("Save changes").click());
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("another window");
  expect((document.getElementById("factory-display-name") as HTMLInputElement).value).toBe("Research");
  await act(async () => button("Reload settings").click());
  expect((document.getElementById("factory-display-name") as HTMLInputElement).value).toBe("Acme Factory");
});

it("distinguishes a pending save from an acknowledged runtime update", async () => {
  vi.useFakeTimers();
  const fetch = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      settings = { ...settings, ...JSON.parse(String(init.body)), revision: 1, status: "pending", applied_revision: null, error: "Factory is offline" };
      return { ok: true, json: async () => settings };
    }
    return fetch(path, init);
  }));
  await fill("factory-display-name", "Research");
  await act(async () => button("Save changes").click());
  expect(document.body.textContent).toContain("Saved · waiting for Factory to apply");
  settings = { ...settings, status: "applied", applied_revision: 1, error: null };
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(document.body.textContent).toContain("Applied to Factory");
});

it("supports renaming a Factory with no Teams source", async () => {
  settings = { ...settings, sources: [] };
  await act(async () => root.render(<FactorySettingsDialog open={false} onOpenChange={() => {}} />));
  await act(async () => root.render(<FactorySettingsDialog open onOpenChange={() => {}} />));
  expect(document.body.textContent).toContain("No Teams activation sources");
  await fill("factory-display-name", "Research");
  expect(button("Save changes").disabled).toBe(false);
});
