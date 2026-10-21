import React, { useEffect } from "react";
import { EventEmitter } from "node:events";
import { describe, expect, it, beforeAll, afterEach } from "vitest";

import type { RPCClient } from "../../rpc/client.js";
import { useAppStore } from "../../store/appStore.js";

type InkModule = typeof import("ink");
type InkRender = InkModule["render"];

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

class FakeClient extends EventEmitter {
  private functions = new Map<
    string,
    (args: Record<string, unknown>) => Promise<unknown>
  >();
  approvalResponses: Array<{
    callId: string;
    decision: "approve" | "reject";
    alwaysApprove: boolean;
    rejectionMessage?: string;
  }> = [];

  registerClientFunction(
    name: string,
    handler: (args: Record<string, unknown>) => Promise<unknown>,
  ) {
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

  async toolApprovalResponse(
    callId: string,
    decision: "approve" | "reject",
    alwaysApprove = false,
    rejectionMessage?: string,
  ) {
    this.approvalResponses.push({
      callId,
      decision,
      alwaysApprove,
      rejectionMessage,
    });
    return true;
  }
}

let inkRender: InkRender;
let Text: InkModule["Text"];
let useMessagesOptimized: (typeof import("../useMessagesNew.js"))["useMessagesOptimized"];

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
      const api = useMessagesOptimized({ client: rpcClient, rpcReady: true });
      useEffect(() => {
        approveToolCall = api.approveToolCall;
      }, [api.approveToolCall]);
      return <Text>ok</Text>;
    };

    const instance = inkRender(
      <Harness rpcClient={client as unknown as RPCClient} />,
      {
        stdout: process.stdout,
        stderr: process.stderr,
        stdin: process.stdin,
        debug: false,
        exitOnCtrlC: false,
        patchConsole: false,
      },
    );
    instances.push(instance);

    await flush();
    expect(approveToolCall).toBeTruthy();

    client.emit("tool_approval_requested", {
      call_id: "a1",
      tool_name: "execute_bash",
      arguments: "cmd: 1",
      session_id: "s",
      run_id: "run",
    });
    client.emit("tool_approval_requested", {
      call_id: "a2",
      tool_name: "execute_bash",
      arguments: "cmd: 2",
      session_id: "s",
      run_id: "run",
    });
    client.emit("tool_approval_requested", {
      call_id: "a3",
      tool_name: "execute_bash",
      arguments: "cmd: 3",
      session_id: "s",
      run_id: "run",
    });

    await flush();
    expect(useAppStore.getState().approval?.call_id).toBe("a1");

    await approveToolCall!(true, false);
    await flush();
    expect(client.approvalResponses[0]).toEqual({
      callId: "a1",
      decision: "approve",
      alwaysApprove: false,
      rejectionMessage: undefined,
    });
    expect(useAppStore.getState().approval?.call_id).toBe("a2");

    await approveToolCall!(false, false);
    await flush();
    expect(client.approvalResponses[1]).toEqual({
      callId: "a2",
      decision: "reject",
      alwaysApprove: false,
      rejectionMessage: "User rejected tool call",
    });
    expect(useAppStore.getState().approval?.call_id).toBe("a3");

    await approveToolCall!(true, true);
    await flush();
    expect(client.approvalResponses[2]).toEqual({
      callId: "a3",
      decision: "approve",
      alwaysApprove: true,
      rejectionMessage: undefined,
    });
    expect(useAppStore.getState().approval).toBeNull();
  });
});
