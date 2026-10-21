import React from "react";
import { EventEmitter } from "node:events";
import { cleanup, render } from "ink-testing-library";
import stripAnsi from "strip-ansi";
import { afterEach, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { GitPanel } from "../GitPanel.js";
import type { RPCClient } from "../../rpc/client.js";

const plain = (frame?: string) => stripAnsi(frame ?? "");

const settle = () => new Promise((r) => setTimeout(r, 60));
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
afterEach(cleanup);
function mount(view: React.ReactNode) {
  const wrap = (node: React.ReactNode) => (
    <KeypressProvider>{node}</KeypressProvider>
  );
  const instance = render(wrap(view));
  return {
    ...instance,
    rerender: (node: React.ReactNode) => instance.rerender(wrap(node)),
  };
}
function client() {
  return Object.assign(new EventEmitter(), {
    defaultEnvironmentId: (): string => "env",
    isGranted: () => true,
    fsList: vi.fn().mockResolvedValue({ entries: [] }),
    fsWatch: vi.fn().mockResolvedValue({ watch_id: "watch" }),
    fsUnwatch: vi.fn().mockResolvedValue(true),
    gitListRepositories: vi
      .fn()
      .mockResolvedValue({ repositories: [{ repo: "repo" }] }),
    gitStatus: vi.fn().mockResolvedValue({
      head: { branch: "main", detached: false, unborn: false },
      entries: [],
      untracked: [],
      conflicted: [],
      ignored: [],
      stash_count: 0,
      clean: true,
      state: "clean",
      upstream: null,
    }),
  });
}
const git = (api: ReturnType<typeof client>, focused = true) => (
  <GitPanel
    focused={focused}
    client={api as unknown as RPCClient}
    onUnfocus={() => {}}
  />
);

it("does not issue a follow-up Git status request after its view has closed", async () => {
  const api = client();
  const listing = deferred<unknown>();
  api.gitListRepositories.mockReturnValue(listing.promise);
  const ui = mount(git(api));
  await settle();
  ui.rerender(git(api, false));
  await settle();
  listing.resolve({ repositories: [{ repo: "old" }] });
  await settle();
  expect(api.gitStatus).not.toHaveBeenCalled();
});

it("keeps the latest Git refresh when status responses arrive out of order", async () => {
  const api = client();
  const old = deferred<unknown>();
  const status = {
    head: { branch: "main", detached: false, unborn: false },
    entries: [],
    untracked: ["new-result.txt"],
    conflicted: [],
    ignored: [],
    stash_count: 0,
    clean: false,
    state: "clean",
    upstream: null,
  };
  api.gitStatus.mockReturnValueOnce(old.promise).mockResolvedValue(status);
  const ui = mount(git(api));
  await settle();
  ui.stdin.write("r");
  await settle();
  old.resolve({ ...status, untracked: ["old-result.txt"] });
  await settle();
  expect(plain(ui.lastFrame())).toContain("new-result.txt");
  expect(plain(ui.lastFrame())).not.toContain("old-result.txt");
});

it("starts a fresh repository listing when the environment changes on the same client", async () => {
  const api = client();
  let environment = "first";
  api.defaultEnvironmentId = () => environment;
  api.gitListRepositories.mockImplementation(async (env: string) => ({
    repositories: [{ repo: `${env}-repo` }],
  }));
  const ui = mount(git(api));
  await settle();
  environment = "second";
  ui.rerender(git(api));
  await settle();
  expect(api.gitStatus).toHaveBeenLastCalledWith("second", "second-repo", {
    includeUntracked: true,
  });
});
