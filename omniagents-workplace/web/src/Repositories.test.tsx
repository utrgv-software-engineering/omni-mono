// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RepositoriesDialog, RepositoryView } from "./Repositories";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let root: Root;
let container: HTMLDivElement;
let calls: { path: string; method: string; body: unknown }[];
const settings = { provider: "standin", repositories: ["org/factory"], factory_repository: null, self_improvement: false,
  connected_as: null, oauth_configured: false, client_id: "", callback_url: "http://w/callback", webhook_url: "http://w/events", webhook_configured: false };
beforeEach(async () => {
  calls = [];
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    const value = path.includes("/available") ? { repositories: [{ full_name: "org/factory", html_url: "http://w/github/org/factory" }] }
      : path.endsWith("/github/app") ? { ...settings, oauth_configured: true, client_id: "Iv1.test" }
      : settings;
    return { ok: true, json: async () => value };
  }));
  await act(async () => { root.render(<RepositoriesDialog open onOpenChange={() => {}} />); });
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const select = async (id: string, value: string) => { await act(async () => {
  const el = document.getElementById(id) as HTMLSelectElement;
  el.value = value; el.dispatchEvent(new Event("change", { bubbles: true }));
}); };
const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent === name)!;

it("requires a linked repository and explicit opt-in", async () => {
  await act(async () => { root.render(<RepositoriesDialog open mode="factory" onOpenChange={() => {}} />); });
  const toggle = () => document.getElementById("self-improvement") as HTMLButtonElement;
  expect(toggle().disabled).toBe(true);
  await select("factory-repository", "org/factory");
  expect(toggle().disabled).toBe(false);
  expect(toggle().getAttribute("aria-checked")).toBe("false");
  await act(async () => toggle().click());
  await act(async () => button("Save").click());
  expect(calls.find(c => c.method === "PUT")?.body).toEqual({ provider: "standin", repositories: ["org/factory"], factory_repository: "org/factory", self_improvement: true });
});

it("switching providers clears the old target and keeps self-improvement off", async () => {
  await select("repository-provider", "github");
  expect(document.getElementById("self-improvement")).toBeNull();
  expect(document.getElementById("factory-repository")).toBeNull();
  expect(button("Connect GitHub").disabled).toBe(true);
  expect(document.getElementById("github-client-secret")?.getAttribute("type")).toBe("password");
  expect(document.querySelector('input[aria-label="Callback URL"]')).not.toBeNull();
});

it("connects using a PAT without app configuration and clears the input", async () => {
  await select("repository-provider", "github");
  expect(document.querySelector("details")?.open).toBe(false);
  const input = document.getElementById("github-token") as HTMLInputElement;
  expect(input.type).toBe("password");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "github_pat_test");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(button("Connect GitHub").disabled).toBe(false);
  await act(async () => button("Connect GitHub").click());
  expect(calls.find(c => c.path.endsWith("/github/token"))?.body).toEqual({ token: "github_pat_test" });
  expect(input.value).toBe("");
  expect(calls.some(c => c.path.endsWith("/github/app") || c.path.endsWith("/github/connect"))).toBe(false);
});

it("shows a device code and loads the connected account after approval", async () => {
  await select("repository-provider", "github");
  const originalFetch = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/github/device")) return {ok: true, json: async () => ({id:"attempt", status:"pending", user_code:"ABCD-1234", verification_uri:"https://github.com/login/device"})};
    if (path.endsWith("/github/device/attempt")) return {ok: true, json: async () => init?.method === "DELETE" ? {ok:true} : {status:"connected", settings:{...settings, provider:"github", connected_as:"device-user"}}};
    return originalFetch(path, init);
  }));
  vi.useFakeTimers();
  try {
    await act(async () => button("Sign in with GitHub").click());
    expect((document.getElementById("github-device-code") as HTMLInputElement).value).toBe("ABCD-1234");
    expect(document.querySelector('a[href="https://github.com/login/device"]')).not.toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(document.body.textContent).toContain("Connected as device-user");
    expect(document.getElementById("github-device-code")).toBeNull();
    expect(document.getElementById("self-improvement")).toBeNull();
  } finally { vi.useRealTimers(); }
});

it("restores the previous provider's repository selection without re-enabling self-improvement", async () => {
  await select("repository-provider", "github");
  await select("repository-provider", "standin");
  expect(document.getElementById("repo-org/factory")).toBeNull();
  await act(async () => button("Save").click());
  expect(calls.find(c => c.method === "PUT")?.body).toEqual({provider:"standin", repositories:["org/factory"], factory_repository:null, self_improvement:false});
});

it("keeps selection in its own dialog and preserves selected repositories", async () => {
  await act(async () => { root.render(<RepositoriesDialog open mode="repositories" onOpenChange={() => {}} />); });
  expect(document.getElementById("repo-org/factory")?.getAttribute("aria-checked")).toBe("true");
  expect(document.getElementById("repository-provider")).toBeNull();
  expect(document.getElementById("self-improvement")).toBeNull();
  await act(async () => button("Save").click());
  expect(calls.find(c => c.method === "PUT")?.body).toMatchObject({repositories:["org/factory"]});
});

it("searches and paginates connected repositories and opens focused settings", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ok:true,json:async()=>({provider:"github",repositories:Array.from({length:25},(_,i)=>`org/project-${String(i).padStart(2,"0")}`)})})));
  const onSettings=vi.fn();
  await act(async()=>root.render(<RepositoryView admin onSettings={onSettings} standin={()=>null} />));
  expect(container.querySelectorAll("li").length).toBe(20);
  await act(async()=>button("Next").click());
  expect(container.querySelectorAll("li").length).toBe(5);
  const input=container.querySelector('input[type="search"]') as HTMLInputElement;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(input,"project-01");input.dispatchEvent(new Event("input",{bubbles:true}));});
  expect(container.querySelectorAll("li").length).toBe(1);
  expect(container.querySelector('a[aria-label="Open org/project-01 on GitHub"]')?.getAttribute("href")).toBe("https://github.com/org/project-01");
  await act(async()=>button("Connection settings").click());
  expect(onSettings).toHaveBeenLastCalledWith("connection");
  await act(async()=>button("Manage repositories").click());
  expect(onSettings).toHaveBeenLastCalledWith("repositories");
});
