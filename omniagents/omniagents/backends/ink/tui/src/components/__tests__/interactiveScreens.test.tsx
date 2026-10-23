import React from "react";
import { cleanup, render } from "ink-testing-library";
import stripAnsi from "strip-ansi";
import { afterEach, expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { AgentsScreen } from "../AgentsScreen.js";
import { ApprovalDialog } from "../ApprovalDialog.js";
import { McpApprovalDialog } from "../McpApprovalDialog.js";
import { ElicitationDialog } from "../ElicitationDialog.js";
import type { SubagentSummary } from "@omniagents/client/transcript/activity-store";
import { useAppStore } from "../../store/appStore.js";
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

const initial = useAppStore.getState();
afterEach(() => useAppStore.setState(initial, true));

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
  ui.stdin.write("\x1b[B");
  await settle();
  useAppStore
    .getState()
    .actions.setSubagents([worker("a", "completed"), worker("b", "running")]);
  await settle();
  expect(plain(ui.lastFrame())).toContain("DETAIL_b");
  expect(plain(ui.lastFrame())).not.toContain("DETAIL_a");
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
    ui.stdin.write("\x1b[200~1\x1b[201~");
    await settle();
    expect(old).not.toHaveBeenCalled();
    expect(next).not.toHaveBeenCalled();
    ui.stdin.write("1");
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
  expect(plain(ui.lastFrame())).toContain("New metadata");
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
  ui.stdin.write("\x1b[B");
  await settle();
  ui.rerender(view(["beta", "alpha"]));
  await settle();
  ui.stdin.write("\r");
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
  expect(plain(ui.lastFrame())).toContain("runtime stopped");
  expect(plain(ui.lastFrame())).toContain("Parent notification: queued");
  expect(plain(ui.lastFrame())).toContain("r recover");
  ui.stdin.write("\x1b[200~r\x1b[201~");
  await settle();
  expect(action).not.toHaveBeenCalled();
  ui.stdin.write("r");
  ui.stdin.write("r");
  await settle();
  expect(action).toHaveBeenCalledTimes(1);
  expect(action).toHaveBeenCalledWith("w", "resume");
  pending.resolve();
  await settle();
  action.mockRejectedValueOnce(new Error("workspace unavailable"));
  ui.stdin.write("r");
  await settle();
  expect(plain(ui.lastFrame())).toContain("workspace unavailable");
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
  expect(plain(ui.lastFrame())).not.toContain("r recover");
  ui.stdin.write("x");
  await settle();
  expect(action).toHaveBeenCalledTimes(1);
  expect(action).toHaveBeenCalledWith("w", "close");
  useAppStore.getState().actions.setSubagents([{ ...entry, status: "closed" }]);
  await settle();
  expect(plain(ui.lastFrame())).toContain("closed");
  expect(plain(ui.lastFrame())).not.toContain("x close");
  ui.stdin.write("r");
  ui.stdin.write("x");
  await settle();
  expect(action).toHaveBeenCalledTimes(1);
});

it("Ink opens providers from /model, cancels safely, and confirms one workspace change", async () => {
  const { ModelPickerPanel } = await import("../ModelPickerPanel.js");
  const pending = deferred<Record<string, unknown>>();
  const listModels = vi.fn().mockResolvedValue({
    models: [],
    default_model: null,
    voice_default_model: null,
    errors: [],
    reasons: [],
    session: null,
  });
  const api = {
    request: (method: string) =>
      method === "list_models"
        ? listModels()
        : Promise.reject(new Error(`unexpected ${method}`)),
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
  expect(plain(ui.lastFrame())).toContain("p provider");
  ui.stdin.write("p");
  await settle();
  expect(api.serverCall).toHaveBeenCalledWith("model_providers", {}, "parent");
  expect(plain(ui.lastFrame())).toContain("Workspace provider");
  ui.stdin.write("\x1b[B");
  ui.stdin.write("\r");
  await settle();
  expect(plain(ui.lastFrame())).toContain(
    "Continuing conversations will send their history to personal",
  );
  ui.stdin.write("n");
  await settle();
  expect(api.serverCall).toHaveBeenCalledTimes(1);
  ui.stdin.write("\r");
  await settle();
  ui.stdin.write("\x1b[200~y\x1b[201~");
  await settle();
  expect(api.serverCall).toHaveBeenCalledTimes(1);
  ui.stdin.write("y");
  ui.stdin.write("y");
  await settle();
  expect(api.serverCall).toHaveBeenCalledTimes(2);
  expect(api.serverCall).toHaveBeenLastCalledWith(
    "switch_provider",
    { model: "personal/c", expected_provider: "company", confirm: true },
    "parent",
  );
  pending.resolve({ ok: true, provider: "personal", model: "personal/c" });
  await settle();
  expect(plain(ui.lastFrame())).toContain("Models");
  expect(listModels).toHaveBeenCalledTimes(2);
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
  ui.stdin.write("\r");
  await settle();
  ui.stdin.write("y");
  await settle();
  expect(plain(ui.lastFrame())).toContain("Refresh the list and confirm again");
  expect(close).not.toHaveBeenCalled();
});
