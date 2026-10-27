import React from "react";
import { cleanup, render } from "ink-testing-library";
import stripAnsi from "strip-ansi";
import { afterEach, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { GitPanel } from "../GitPanel.js";
import type { RPCClient } from "@omniagents/client/rpc/client";

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
function status(environment: string, repo: string, untracked: string[] = []) {
  return {
    environment_id: environment,
    repo,
    head: { branch: "main", detached: false, unborn: false, oid: "abc" },
    entries: [],
    untracked,
    conflicted: [],
    ignored: [],
    stash_count: 0,
    clean: untracked.length === 0,
    state: "clean",
    upstream: null,
  };
}

function repositories(environment: string, repos: string[]) {
  return {
    environment_id: environment,
    path: ".",
    repositories: repos.map((repo) => ({
      repo,
      root: repo,
      absolute_root: `/workspace/${repo}`,
      branch: "main",
      detached: false,
      bare: false,
      git_common_dir: null,
      is_linked_worktree: false,
      source: null,
    })),
    sources: [],
    unreachable_sources: [],
    max_depth: 3,
    max_depth_clamped: false,
    truncated: false,
  };
}

/** The shared client as GitPanel sees it: one request() per wire method. */
function client() {
  const api = {
    environment: "env",
    listRepositories: vi.fn(async (params: { environment_id: string }) =>
      repositories(params.environment_id, ["repo"]),
    ),
    status: vi.fn(async (params: { environment_id: string; repo: string }) =>
      status(params.environment_id, params.repo),
    ),
  };
  const rpc = {
    get initializeResult() {
      return {
        agent_host: { default_environment_id: api.environment },
      };
    },
    request: (method: string, params: never) => {
      if (method === "git_list_repositories")
        return api.listRepositories(params);
      if (method === "git_status") return api.status(params);
      if (method === "git_status_watch") return new Promise(() => {});
      return Promise.reject(new Error(`unexpected ${method}`));
    },
    on: () => () => {},
    onConnectionState: () => () => {},
    supportsExperimentalOperation: () => true,
    supportsExperimentalFeature: () => true,
  };
  return { api, rpc };
}
const git = (fake: ReturnType<typeof client>, focused = true) => (
  <GitPanel
    focused={focused}
    client={fake.rpc as unknown as RPCClient}
    onUnfocus={() => {}}
  />
);

it("does not issue a follow-up Git status request after its view has closed", async () => {
  const fake = client();
  const listing = deferred<unknown>();
  fake.api.listRepositories.mockReturnValue(listing.promise as never);
  const ui = mount(git(fake));
  await settle();
  ui.rerender(git(fake, false));
  await settle();
  listing.resolve(repositories("env", ["old"]));
  await settle();
  expect(fake.api.status).not.toHaveBeenCalled();
});

it("keeps the latest Git refresh when status responses arrive out of order", async () => {
  const fake = client();
  const old = deferred<unknown>();
  fake.api.status
    .mockReturnValueOnce(old.promise as never)
    .mockResolvedValue(status("env", "repo", ["new-result.txt"]));
  const ui = mount(git(fake));
  await settle();
  ui.stdin.write("r");
  await settle();
  old.resolve(status("env", "repo", ["old-result.txt"]));
  await settle();
  expect(plain(ui.lastFrame())).toContain("new-result.txt");
  expect(plain(ui.lastFrame())).not.toContain("old-result.txt");
});

it("starts a fresh repository listing when the environment changes on the same client", async () => {
  const fake = client();
  fake.api.environment = "first";
  fake.api.listRepositories.mockImplementation(async (params) =>
    repositories(params.environment_id, [`${params.environment_id}-repo`]),
  );
  const ui = mount(git(fake));
  await settle();
  fake.api.environment = "second";
  ui.rerender(git(fake));
  await settle();
  expect(fake.api.status).toHaveBeenLastCalledWith(
    expect.objectContaining({
      environment_id: "second",
      repo: "second-repo",
      include_untracked: true,
    }),
  );
});
