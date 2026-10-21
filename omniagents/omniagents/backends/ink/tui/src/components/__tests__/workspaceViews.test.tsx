import React from "react";
import { EventEmitter } from "node:events";
import { createHash } from "node:crypto";
import { cleanup, render } from "ink-testing-library";
import stripAnsi from "strip-ansi";
import { afterEach, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { FilesScreen } from "../FilesScreen.js";
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
const files = (api: ReturnType<typeof client>, active = true) => (
  <FilesScreen
    active={active}
    height={30}
    client={api as unknown as RPCClient}
    onExit={() => {}}
    onMention={() => {}}
  />
);
const git = (api: ReturnType<typeof client>, focused = true) => (
  <GitPanel
    focused={focused}
    client={api as unknown as RPCClient}
    onUnfocus={() => {}}
  />
);

it("releases a watch that finishes after leaving Files", async () => {
  const api = client();
  const watch = deferred<{ watch_id: string }>();
  api.fsWatch.mockReturnValue(watch.promise);
  const ui = mount(files(api));
  await settle();
  ui.rerender(files(api, false));
  await settle();
  watch.resolve({ watch_id: "late-watch" });
  await settle();
  expect(api.fsUnwatch).toHaveBeenCalledWith("env", "late-watch");
});

it("does not paint an old client's file listing into a new environment", async () => {
  const old = client(),
    next = client();
  const listing = deferred<unknown>();
  old.fsList.mockReturnValue(listing.promise);
  next.fsList.mockResolvedValue({
    entries: [{ path: "new.txt", name: "new.txt", type: "file", size: 1 }],
  });
  const ui = mount(files(old));
  await settle();
  ui.rerender(files(next));
  await settle();
  listing.resolve({
    entries: [{ path: "old.txt", name: "old.txt", type: "file", size: 1 }],
  });
  await settle();
  expect(plain(ui.lastFrame())).toContain("new.txt");
  expect(plain(ui.lastFrame())).not.toContain("old.txt");
});

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

it("preserves edits typed during an upload and protects them with the newly saved checksum", async () => {
  const api = client();
  const commit = deferred<unknown>();
  const sha = (text: string) => createHash("sha256").update(text).digest("hex");
  Object.assign(api, {
    fsDownloadOpen: vi.fn().mockResolvedValue({
      transfer_id: "download",
      size: 3,
      sha256: sha("old"),
    }),
    fsDownloadRead: vi.fn().mockResolvedValue({
      offset: 0,
      bytes: 3,
      data: Buffer.from("old").toString("base64"),
      eof: true,
    }),
    fsDownloadClose: vi.fn().mockResolvedValue(true),
    fsUploadOpen: vi
      .fn()
      .mockResolvedValue({ transfer_id: "upload", chunk_size: 100 }),
    fsUploadChunk: vi.fn().mockResolvedValue({}),
    fsUploadCommit: vi
      .fn()
      .mockReturnValueOnce(commit.promise)
      .mockResolvedValue({}),
    fsUploadAbort: vi.fn().mockResolvedValue({}),
  });
  api.fsList.mockResolvedValue({
    entries: [{ path: "file.txt", name: "file.txt", type: "file", size: 3 }],
  });
  const ui = mount(files(api));
  await settle();
  ui.stdin.write("e");
  await settle();
  ui.stdin.write("a");
  await settle();
  ui.stdin.write("\x13");
  await settle();
  ui.stdin.write("b");
  await settle();
  commit.resolve({});
  await settle();
  expect(plain(ui.lastFrame())).toContain("abold");
  ui.stdin.write("\x1b");
  await settle();
  // Dirty edits must require a second Escape to discard after the older save completes.
  expect(plain(ui.lastFrame())).toContain("abold");
  ui.stdin.write("\x13");
  await settle();
  const open = (api as unknown as { fsUploadOpen: ReturnType<typeof vi.fn> })
    .fsUploadOpen;
  expect(open).toHaveBeenCalledTimes(2);
  expect(open.mock.calls[1][3].expectedSha256).toBe(sha("aold"));
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
