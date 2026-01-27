import React, { useEffect } from "react";
import { EventEmitter } from "node:events";
import { describe, expect, it, beforeAll, afterEach } from "vitest";

import type { RPCClient } from "../../rpc/client.js";
import { useAppStore } from "../../store/appStore.js";

type InkModule = typeof import("ink");
type InkRender = InkModule["render"];

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

class FakeClient extends EventEmitter {
  private functions = new Map<string, (args: Record<string, unknown>) => Promise<unknown>>();

  registerClientFunction(name: string, handler: (args: Record<string, unknown>) => Promise<unknown>) {
    this.functions.set(name, handler);
  }

  unregisterClientFunction(name: string) {
    this.functions.delete(name);
  }

  async callClientFunction(name: string, args: Record<string, unknown>) {
    const handler = this.functions.get(name);
    if (!handler) throw new Error(`missing handler: ${name}`);
    return handler(args);
  }
}

let inkRender: InkRender;
let Text: InkModule["Text"];
let useMessagesOptimized: typeof import("../useMessagesNew.js")["useMessagesOptimized"];

const instances: Array<ReturnType<InkRender>> = [];

beforeAll(async () => {
  const inkModule = await import("ink");
  inkRender = inkModule.render;
  Text = inkModule.Text;
  ({ useMessagesOptimized } = await import("../useMessagesNew.js"));
});

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
  useAppStore.getState().actions.clearMessages();
});

describe("approval queue", () => {
  it("queues parallel approval requests and resolves in order", async () => {
    const client = new FakeClient();

    let approveToolCall:
      | ((approved: boolean, alwaysApprove: boolean) => Promise<void>)
      | null = null;

    const Harness = ({ rpcClient }: { rpcClient: RPCClient }) => {
      const api = useMessagesOptimized({ client: rpcClient });
      useEffect(() => {
        approveToolCall = api.approveToolCall;
      }, [api.approveToolCall]);
      return <Text>ok</Text>;
    };

    const instance = inkRender(<Harness rpcClient={client as unknown as RPCClient} />, {
      stdout: process.stdout,
      stderr: process.stderr,
      stdin: process.stdin,
      debug: false,
      exitOnCtrlC: false,
      patchConsole: false,
    });
    instances.push(instance);

    await flush();
    expect(approveToolCall).toBeTruthy();

    const p1 = client.callClientFunction("ui.request_tool_approval", {
      request_id: "r1",
      approval_id: "a1",
      tool: "execute_bash",
      arguments: "cmd: 1",
      session_id: "s",
      run_id: "run",
    });
    const p2 = client.callClientFunction("ui.request_tool_approval", {
      request_id: "r2",
      approval_id: "a2",
      tool: "execute_bash",
      arguments: "cmd: 2",
      session_id: "s",
      run_id: "run",
    });
    const p3 = client.callClientFunction("ui.request_tool_approval", {
      request_id: "r3",
      approval_id: "a3",
      tool: "execute_bash",
      arguments: "cmd: 3",
      session_id: "s",
      run_id: "run",
    });

    await flush();
    expect(useAppStore.getState().approval?.request_id).toBe("r1");

    await approveToolCall!(true, false);
    await flush();
    expect(await p1).toEqual({ approved: true, always_approve: false });
    expect(useAppStore.getState().approval?.request_id).toBe("r2");

    await approveToolCall!(false, false);
    await flush();
    expect(await p2).toEqual({
      approved: false,
      always_approve: false,
      error_message: "User rejected tool call",
    });
    expect(useAppStore.getState().approval?.request_id).toBe("r3");

    await approveToolCall!(true, true);
    await flush();
    expect(await p3).toEqual({ approved: true, always_approve: true });
    expect(useAppStore.getState().approval).toBeNull();
  });
});
