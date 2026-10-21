import React from "react";
import { EventEmitter } from "node:events";
import { Writable } from "node:stream";
import { render } from "ink";
import xterm from "@xterm/headless";
import { afterEach, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { ReviewScreen } from "../ReviewScreen.js";
import { AgentsScreen } from "../AgentsScreen.js";
import { ApprovalDialog } from "../ApprovalDialog.js";
import { McpApprovalDialog } from "../McpApprovalDialog.js";
import { ElicitationDialog } from "../ElicitationDialog.js";
import { useAppStore, type SubagentSummary } from "../../store/appStore.js";
import type { RPCClient } from "../../rpc/client.js";

const settle = () => new Promise((r) => setTimeout(r, 60));
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
class Input extends EventEmitter {
  isTTY = true;
  queue: string[] = [];
  setRawMode() {}
  setEncoding() {}
  resume() {}
  pause() {}
  ref() {}
  unref() {}
  read() {
    return this.queue.shift() ?? null;
  }
  press(value: string) {
    this.queue.push(value);
    this.emit("readable");
  }
}
const cleanup: (() => void)[] = [];
afterEach(() => {
  for (const fn of cleanup.splice(0)) fn();
});
function mount(view: React.ReactNode) {
  const terminal = new xterm.Terminal({
    cols: 100,
    rows: 40,
    convertEol: true,
    allowProposedApi: true,
  });
  const stdout = new Writable({
    write(chunk, _encoding, done) {
      terminal.write(chunk, done);
    },
  });
  Object.assign(stdout, { columns: 100, rows: 40, isTTY: true });
  const stdin = new Input();
  const wrap = (node: React.ReactNode) => (
    <KeypressProvider>{node}</KeypressProvider>
  );
  const instance = render(wrap(view), {
    stdout: stdout as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stderr: stdout as unknown as NodeJS.WriteStream,
    exitOnCtrlC: false,
  });
  cleanup.push(() => {
    instance.unmount();
    terminal.dispose();
  });
  return {
    stdin,
    rerender: (node: React.ReactNode) => instance.rerender(wrap(node)),
    screen: () =>
      Array.from(
        { length: terminal.buffer.active.length },
        (_, i) =>
          terminal.buffer.active.getLine(i)?.translateToString(true) ?? "",
      ).join("\n"),
  };
}

const initial = useAppStore.getState();
afterEach(() => useAppStore.setState(initial, true));
function reviewClient() {
  return Object.assign(new EventEmitter(), {
    defaultEnvironmentId: () => "env",
    isGranted: () => true,
    gitListRepositories: vi
      .fn()
      .mockResolvedValue({ repositories: [{ repo: "repo" }] }),
    gitStatus: vi.fn().mockResolvedValue({
      entries: [],
      untracked: ["a.txt", "b.txt"],
      conflicted: [],
    }),
    gitDiff: vi.fn().mockResolvedValue({ files: [] }),
    gitStage: vi.fn().mockReturnValue(new Promise(() => {})),
    gitUnstage: vi.fn(),
    gitDiscard: vi.fn(),
    gitCommit: vi.fn(),
  });
}
const review = (api: ReturnType<typeof reviewClient>, active = true) => (
  <ReviewScreen
    active={active}
    height={30}
    client={api as unknown as RPCClient}
    onExit={() => {}}
  />
);

it("Review stages the newly selected file exactly once during a key burst", async () => {
  const api = reviewClient();
  const ui = mount(review(api));
  await settle();
  ui.stdin.press("\x1b[B  ");
  await settle();
  expect(api.gitStage).toHaveBeenCalledTimes(1);
  expect(api.gitStage).toHaveBeenCalledWith("env", "repo", {
    paths: ["b.txt"],
  });
});

it("Review treats pasted commands as text rather than actions", async () => {
  const api = reviewClient();
  const ui = mount(review(api));
  await settle();
  for (const key of [" ", "x", "c"]) ui.stdin.press(`\x1b[200~${key}\x1b[201~`);
  await settle();
  expect(api.gitStage).not.toHaveBeenCalled();
  expect(api.gitDiscard).not.toHaveBeenCalled();
  expect(ui.screen()).not.toContain("commit: ");
});

it("Review ignores a listing that resolves after the screen closes", async () => {
  const api = reviewClient();
  const listing = deferred<unknown>();
  api.gitListRepositories.mockReturnValue(listing.promise);
  const ui = mount(review(api));
  await settle();
  ui.rerender(review(api, false));
  await settle();
  listing.resolve({ repositories: [{ repo: "late" }] });
  await settle();
  expect(api.gitStatus).not.toHaveBeenCalled();
});

it("Review keeps selection identity when refreshed files reorder", async () => {
  const api = reviewClient();
  const ui = mount(review(api));
  await settle();
  ui.stdin.press("\x1b[B");
  await settle();
  api.gitStatus.mockResolvedValue({
    entries: [],
    untracked: ["b.txt", "a.txt"],
    conflicted: [],
  });
  ui.stdin.press("r");
  await settle();
  ui.stdin.press(" ");
  await settle();
  expect(api.gitStage).toHaveBeenCalledWith("env", "repo", {
    paths: ["b.txt"],
  });
});

it("Agents keeps the selected worker when completion changes the sort order", async () => {
  const worker = (id: string, status: "running" | "completed") =>
    ({
      subagent_id: id,
      status,
      task: `task ${id}`,
      started_at: id === "a" ? 1 : 2,
      result: `DETAIL_${id}`,
      kind: "worker",
    }) as SubagentSummary;
  useAppStore
    .getState()
    .actions.setSubagents([worker("a", "running"), worker("b", "running")]);
  const ui = mount(<AgentsScreen active height={30} onExit={() => {}} />);
  await settle();
  ui.stdin.press("\x1b[B");
  await settle();
  useAppStore
    .getState()
    .actions.setSubagents([worker("a", "completed"), worker("b", "running")]);
  await settle();
  expect(ui.screen()).toContain("DETAIL_b");
  expect(ui.screen()).not.toContain("DETAIL_a");
});

it.each(["tool", "mcp", "elicitation"])(
  "%s dialog uses updated callbacks and ignores pasted choices",
  async (kind) => {
    const old = vi.fn(),
      next = vi.fn();
    const view = (respond: ReturnType<typeof vi.fn>) =>
      kind === "tool" ? (
        <ApprovalDialog tool="demo" arguments="{}" onApprove={respond} />
      ) : kind === "mcp" ? (
        <McpApprovalDialog
          tool="demo"
          serverLabel="server"
          arguments="{}"
          onApprove={respond}
        />
      ) : (
        <ElicitationDialog
          elicitation={{
            elicitation_id: "same",
            kind: "confirm",
            message: "confirm",
          }}
          onRespond={respond}
        />
      );
    const ui = mount(view(old));
    await settle();
    ui.rerender(view(next));
    await settle();
    ui.stdin.press("\x1b[200~1\x1b[201~");
    await settle();
    expect(old).not.toHaveBeenCalled();
    expect(next).not.toHaveBeenCalled();
    ui.stdin.press("1");
    await settle();
    expect(next).toHaveBeenCalledTimes(1);
    expect(old).not.toHaveBeenCalled();
  },
);

it("can update an approval from plain arguments to rich metadata without violating hook order", async () => {
  const callback = vi.fn();
  const ui = mount(
    <ApprovalDialog tool="demo" arguments="{}" onApprove={callback} />,
  );
  await settle();
  ui.rerender(
    <ApprovalDialog
      tool="demo"
      arguments="{}"
      metadata={{ display_type: "command", summary: "New metadata" }}
      onApprove={callback}
    />,
  );
  await settle();
  expect(ui.screen()).toContain("New metadata");
});

it("keeps the selected elicitation value when refreshed options reorder", async () => {
  const respond = vi.fn();
  const view = (options: string[]) => (
    <ElicitationDialog
      elicitation={{
        elicitation_id: "same",
        kind: "select",
        message: "choose",
        options,
      }}
      onRespond={respond}
    />
  );
  const ui = mount(view(["alpha", "beta"]));
  await settle();
  ui.stdin.press("\x1b[B");
  await settle();
  ui.rerender(view(["beta", "alpha"]));
  await settle();
  ui.stdin.press("\r");
  await settle();
  expect(respond).toHaveBeenCalledWith({
    action: "accept",
    value: { selected: ["beta"] },
  });
});

it("Agents recovers once during key bursts and shows recovery failures", async () => {
  const entry = {
    subagent_id: "w",
    worker_id: "w",
    kind: "worker",
    status: "interrupted",
    task: "recover task",
    recovered: true,
    recovery_reason: "runtime stopped",
    report_status: "queued",
  } as SubagentSummary;
  useAppStore.getState().actions.setSubagents([entry]);
  const pending = deferred<void>();
  const action = vi.fn().mockReturnValue(pending.promise);
  const ui = mount(
    <AgentsScreen
      active
      height={30}
      onExit={() => {}}
      onWorkerAction={action}
    />,
  );
  await settle();
  expect(ui.screen()).toContain("runtime stopped");
  expect(ui.screen()).toContain("Parent notification: queued");
  expect(ui.screen()).toContain("r recover");
  ui.stdin.press("\x1b[200~r\x1b[201~");
  await settle();
  expect(action).not.toHaveBeenCalled();
  ui.stdin.press("r");
  ui.stdin.press("r");
  await settle();
  expect(action).toHaveBeenCalledTimes(1);
  expect(action).toHaveBeenCalledWith("w", "resume");
  pending.resolve();
  await settle();
  action.mockRejectedValueOnce(new Error("workspace unavailable"));
  ui.stdin.press("r");
  await settle();
  expect(ui.screen()).toContain("workspace unavailable");
});

it("Agents closes idle workers and offers no actions on closed workers", async () => {
  const entry = {
    subagent_id: "w",
    worker_id: "w",
    kind: "worker",
    status: "completed",
    task: "idle task",
  } as SubagentSummary;
  useAppStore.getState().actions.setSubagents([entry]);
  const action = vi.fn().mockResolvedValue(undefined);
  const ui = mount(
    <AgentsScreen
      active
      height={30}
      onExit={() => {}}
      onWorkerAction={action}
    />,
  );
  await settle();
  expect(ui.screen()).not.toContain("r recover");
  ui.stdin.press("x");
  await settle();
  expect(action).toHaveBeenCalledTimes(1);
  expect(action).toHaveBeenCalledWith("w", "close");
  useAppStore.getState().actions.setSubagents([{ ...entry, status: "closed" }]);
  await settle();
  expect(ui.screen()).toContain("closed");
  expect(ui.screen()).not.toContain("x close");
  ui.stdin.press("r");
  ui.stdin.press("x");
  await settle();
  expect(action).toHaveBeenCalledTimes(1);
});

it("Ink opens providers from /model, cancels safely, and confirms one workspace change", async () => {
  const { ModelPickerPanel } = await import("../ModelPickerPanel.js");
  const pending = deferred<Record<string, unknown>>();
  const api = {
    listModels: vi.fn().mockResolvedValue({ models: [], errors: [] }),
    serverCall: vi.fn((name: string) =>
      name === "model_providers"
        ? Promise.resolve({
            current: "company",
            providers: [
              { name: "company", model: "company/a", label: "Company model" },
              {
                name: "personal",
                model: "personal/c",
                label: "Personal model",
              },
            ],
          })
        : pending.promise,
    ),
  };
  const ui = mount(
    <ModelPickerPanel
      open
      client={api as unknown as RPCClient}
      sessionId="parent"
      onClose={() => {}}
    />,
  );
  await settle();
  expect(ui.screen()).toContain("p provider");
  ui.stdin.press("p");
  await settle();
  expect(api.serverCall).toHaveBeenCalledWith("model_providers", {}, "parent");
  expect(ui.screen()).toContain("Workspace provider");
  ui.stdin.press("\x1b[B");
  ui.stdin.press("\r");
  await settle();
  expect(ui.screen()).toContain(
    "Continuing conversations will send their history to personal",
  );
  ui.stdin.press("n");
  await settle();
  expect(api.serverCall).toHaveBeenCalledTimes(1);
  ui.stdin.press("\r");
  await settle();
  ui.stdin.press("\x1b[200~y\x1b[201~");
  await settle();
  expect(api.serverCall).toHaveBeenCalledTimes(1);
  ui.stdin.press("y");
  ui.stdin.press("y");
  await settle();
  expect(api.serverCall).toHaveBeenCalledTimes(2);
  expect(api.serverCall).toHaveBeenLastCalledWith(
    "switch_provider",
    { model: "personal/c", expected_provider: "company", confirm: true },
    "parent",
  );
  pending.resolve({ ok: true, provider: "personal", model: "personal/c" });
  await settle();
  expect(ui.screen()).toContain("Models");
  expect(api.listModels).toHaveBeenCalledTimes(2);
});

it("Ink displays provider switch rejection without treating it as success", async () => {
  const { ProviderPickerPanel } = await import("../ProviderPickerPanel.js");
  const close = vi.fn();
  const api = {
    serverCall: vi.fn((name: string) =>
      Promise.resolve(
        name === "model_providers"
          ? {
              current: "company",
              providers: [
                { name: "personal", model: "personal/c", label: "Personal" },
              ],
            }
          : {
              ok: false,
              error:
                "The workspace provider changed. Refresh the list and confirm again.",
            },
      ),
    ),
  };
  const ui = mount(
    <ProviderPickerPanel
      client={api as unknown as RPCClient}
      onClose={close}
    />,
  );
  await settle();
  ui.stdin.press("\r");
  await settle();
  ui.stdin.press("y");
  await settle();
  expect(ui.screen()).toContain("Refresh the list and confirm again");
  expect(close).not.toHaveBeenCalled();
});
