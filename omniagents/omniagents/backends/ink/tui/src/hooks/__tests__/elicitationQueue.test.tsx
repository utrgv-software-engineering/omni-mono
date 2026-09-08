import React, { useEffect } from "react";
import { EventEmitter } from "node:events";
import { describe, expect, it, beforeAll, afterEach, vi } from "vitest";

import type { RPCClient } from "../../rpc/client.js";
import { useAppStore } from "../../store/appStore.js";
import type { ElicitationAnswer } from "../../components/ElicitationDialog.js";
import {
  dialogSupport,
  normalizeOptions,
} from "../../components/ElicitationDialog.js";

type InkModule = typeof import("ink");
type InkRender = InkModule["render"];

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

class FakeClient extends EventEmitter {
  registerClientFunction() {}
  unregisterClientFunction() {}
  elicitationResponses: Array<{
    elicitationId: string;
    action: string;
    value?: Record<string, unknown>;
    reason?: string;
  }> = [];

  async elicitationResponse(
    elicitationId: string,
    action: "accept" | "decline" | "cancel",
    value?: Record<string, unknown>,
    reason?: string,
  ) {
    this.elicitationResponses.push({ elicitationId, action, value, reason });
    return { elicitation_id: elicitationId, status: "accepted" };
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

function mountHarness(client: FakeClient) {
  let respondElicitation:
    | ((answer: ElicitationAnswer, expectedId?: string) => Promise<boolean>)
    | null = null;
  const Harness = ({ rpcClient }: { rpcClient: RPCClient }) => {
    const api = useMessagesOptimized({ client: rpcClient, rpcReady: true });
    useEffect(() => {
      respondElicitation = api.respondElicitation;
    }, [api.respondElicitation]);
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
  return () => respondElicitation;
}

describe("elicitation queue", () => {
  it("ignores a late rejection after another client resolves the prompt", async () => {
    const client = new FakeClient();
    const getRespond = mountHarness(client);
    await flush();
    client.emit("elicitation_requested", { elicitation_id: "race", kind: "confirm", message: "Proceed?", session_id: "s" });
    await flush();
    let reject!: (error: Error) => void;
    vi.spyOn(client, "elicitationResponse").mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
    const sending = getRespond()!({ action: "accept" });
    client.emit("elicitation_resolved", { elicitation_id: "race", status: "accepted", session_id: "s" });
    await flush();
    const status = useAppStore.getState().status;
    reject(new Error("Already resolved by another client"));
    await sending;
    expect(useAppStore.getState().elicitation).toBeNull();
    expect(useAppStore.getState().status).toBe(status);
  });
  it("does not answer a newer prompt through a callback bound to the old one", async () => {
    const client = new FakeClient(); const getRespond = mountHarness(client); await flush();
    client.emit("elicitation_requested", { elicitation_id: "old", kind: "confirm", message: "old" });
    client.emit("elicitation_requested", { elicitation_id: "new", kind: "confirm", message: "new" });
    await flush();
    client.emit("elicitation_resolved", { elicitation_id: "old", status: "accepted" });
    await getRespond()!({ action: "accept" }, "old");
    expect(client.elicitationResponses).toHaveLength(0);
    expect(useAppStore.getState().elicitation?.elicitation_id).toBe("new");
  });

  it("keeps an answerable prompt after a failed send and allows retry", async () => {
    const client = new FakeClient();
    const getRespond = mountHarness(client);
    await flush();
    client.emit("elicitation_requested", {
      elicitation_id: "retry",
      kind: "confirm",
      message: "Proceed?",
      session_id: "s",
    });
    await flush();
    const send = vi
      .spyOn(client, "elicitationResponse")
      .mockRejectedValueOnce(new Error("connection lost"));
    await getRespond()!({ action: "accept" });
    expect(useAppStore.getState().elicitation?.elicitation_id).toBe("retry");
    expect(useAppStore.getState().status).toContain("Please retry");
    await getRespond()!({ action: "accept" });
    expect(send).toHaveBeenCalledTimes(2);
    expect(useAppStore.getState().elicitation).toBeNull();
  });

  it("queues prompts, answers in order, and advances on resolution", async () => {
    const client = new FakeClient();
    const getRespond = mountHarness(client);
    await flush();
    expect(getRespond()).toBeTruthy();

    client.emit("elicitation_requested", {
      elicitation_id: "e1",
      kind: "confirm",
      message: "First?",
      session_id: "s",
    });
    client.emit("elicitation_requested", {
      elicitation_id: "e2",
      kind: "select",
      message: "Second?",
      options: ["a", "b"],
      session_id: "s",
    });
    await flush();
    expect(useAppStore.getState().elicitation?.elicitation_id).toBe("e1");

    await getRespond()!({ action: "accept", value: { confirmed: true } });
    await flush();
    expect(client.elicitationResponses[0]).toEqual({
      elicitationId: "e1",
      action: "accept",
      value: { confirmed: true },
      reason: undefined,
    });
    expect(useAppStore.getState().elicitation?.elicitation_id).toBe("e2");

    // Another client wins the race on e2: the resolution broadcast
    // dismisses the prompt without a local response.
    client.emit("elicitation_resolved", {
      elicitation_id: "e2",
      status: "accepted",
      session_id: "s",
    });
    await flush();
    expect(useAppStore.getState().elicitation).toBeNull();
    expect(client.elicitationResponses).toHaveLength(1);
  });

  it("drops queued prompts resolved elsewhere (run end clears all)", async () => {
    const client = new FakeClient();
    mountHarness(client);
    await flush();

    client.emit("elicitation_requested", {
      elicitation_id: "e1",
      kind: "confirm",
      message: "One",
    });
    client.emit("elicitation_requested", {
      elicitation_id: "e2",
      kind: "confirm",
      message: "Two",
    });
    await flush();
    client.emit("elicitation_resolved", {
      elicitation_id: "e2",
      status: "cancelled",
    });
    client.emit("elicitation_resolved", {
      elicitation_id: "e1",
      status: "cancelled",
    });
    await flush();
    expect(useAppStore.getState().elicitation).toBeNull();
  });
});

describe("dialog support rules", () => {
  it("renders confirm/select/url and declines forms and unknown kinds", () => {
    const base = { elicitation_id: "e", message: "m" };
    expect(dialogSupport({ ...base, kind: "confirm" }).supported).toBe(true);
    expect(
      dialogSupport({ ...base, kind: "select", options: ["a"] }).supported,
    ).toBe(true);
    expect(
      dialogSupport({ ...base, kind: "url", url: "https://x.test" }).supported,
    ).toBe(true);
    expect(dialogSupport({ ...base, kind: "select" }).supported).toBe(false);
    expect(dialogSupport({ ...base, kind: "url" }).supported).toBe(false);
    expect(dialogSupport({ ...base, kind: "form" }).supported).toBe(false);
    expect(dialogSupport({ ...base, kind: "hologram" }).supported).toBe(false);
  });

  it("normalizes option records and rejects opaque entries", () => {
    expect(
      normalizeOptions(["a", { value: "b", label: "Bee", description: "d" }]),
    ).toEqual([
      { value: "a", label: "a" },
      { value: "b", label: "Bee", description: "d" },
    ]);
    expect(normalizeOptions([{ label: "no value" }])).toBeNull();
    expect(normalizeOptions([])).toBeNull();
  });
});
