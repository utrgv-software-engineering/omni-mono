// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PreviewSetup } from "./PreviewSetup";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const plan = {
  repository: "acme/app", provider: "github", branch: "main", path: "compose.preview.yml", revision: "r1",
  services: [{name: "web", ports: [3000]}, {name: "worker", ports: []}, {name: "postgres", ports: []}],
  variables: ["PREVIEW_DB_PASSWORD", "PREVIEW_SECRET_KEY_BASE", "STRIPE_KEY"],
  generated: ["PREVIEW_DB_PASSWORD", "PREVIEW_SECRET_KEY_BASE"],
  open_pulls: [{number: 75, title: "Event registration"}, {number: 74, title: "Fix login"}],
};

let root: Root, host: HTMLDivElement;
const calls: {action: string; body: Record<string, unknown>}[] = [];
beforeEach(() => {
  calls.length = 0;
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const action = url.split("/").pop()!;
    calls.push({action, body: JSON.parse(String(init.body))});
    return new Response(JSON.stringify(action === "inspect" ? plan : {status: "enabled"}), {status: 200});
  }));
});
afterEach(() => { act(() => root.unmount()); host.remove(); document.body.innerHTML = ""; vi.unstubAllGlobals(); });

const text = () => document.body.textContent || "";
const button = (label: string) => [...document.querySelectorAll("button")].find(b => b.textContent?.trim().startsWith(label))!;
const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });

it("reads the repository on open, asks only for values it cannot generate, and previews the chosen open PRs", async () => {
  await act(async () => root.render(<PreviewSetup repository="acme/app" onClose={() => {}} />));
  await settle();
  expect(calls[0]).toEqual({action: "inspect", body: {repository: "acme/app", path: ""}});
  expect(text()).toContain("compose.preview.yml");
  expect(document.querySelector("#preview-var-STRIPE_KEY")).not.toBeNull();
  expect(document.querySelector("#preview-var-PREVIEW_DB_PASSWORD")).toBeNull();
  // The open PRs stay behind a picker until asked for.
  expect(text()).not.toContain("Event registration");
  expect(button("Enable PR previews").disabled).toBe(true);

  await act(async () => button("Choose from 2 open").click());
  await act(async () => button("#75").click());
  const key = document.querySelector<HTMLInputElement>("#preview-var-STRIPE_KEY")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(key, "sk_test");
    key.dispatchEvent(new Event("input", {bubbles: true}));
  });
  await act(async () => button("Enable PR previews").click());
  await settle();

  expect(calls[1].action).toBe("enable");
  expect(calls[1].body).toMatchObject({services: {web: 3000}, variables: {STRIPE_KEY: "sk_test"}, include: [75]});
  expect(text()).toContain("and #75");
});
