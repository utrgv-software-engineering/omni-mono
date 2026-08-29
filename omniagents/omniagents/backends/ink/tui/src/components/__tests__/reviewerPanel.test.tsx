import React from "react";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { RPCClient } from "../../rpc/client.js";
import type { ReviewerKind } from "../ReviewerPanel.js";

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 120;
  rows = 40;

  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") this.frames.push(chunk);
    });
  }

  lastFrame(): string {
    for (let index = this.frames.length - 1; index >= 0; index -= 1) {
      const frame = stripAnsi(this.frames[index] ?? "");
      if (frame.trim()) return frame;
    }
    return "";
  }
}

class FakeStdin extends EventEmitter {
  isTTY = true;
  private queue: string[] = [];
  setRawMode() {}
  setEncoding() {}
  resume() {}
  pause() {}
  read() {
    return this.queue.shift() ?? null;
  }
  unref() {}
  ref() {}
  press(sequence: string) {
    this.queue.push(sequence);
    this.emit("readable");
  }
}

const KEY = { down: "[B", enter: "\r", escape: "" } as const;
const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

function makeClient(
  opts: {
    approvalsOverride?: string | null;
    workflowOverride?: string | null;
    configFields?: Array<Record<string, unknown>>;
  } = {},
) {
  return {
    listModels: vi.fn(async () => ({
      models: [],
      default_model: null,
      voice_default_model: null,
      errors: [],
      reasons: [],
      session: {
        session_id: "s-1",
        active_model: null,
        reasoning_effort: null,
        approvals_reviewer: opts.approvalsOverride ?? null,
        workflow_reviewer: opts.workflowOverride ?? null,
      },
    })),
    getConfig: vi.fn(async () => ({ fields: opts.configFields ?? [] })),
    // Typed as the wrapper's own return so a test can mock the
    // `{ok: false, reason}` rejection the server really sends.
    setSessionApprovals: vi.fn(
      async (): Promise<Record<string, unknown>> => ({ ok: true }),
    ),
    setSessionWorkflow: vi.fn(
      async (): Promise<Record<string, unknown>> => ({ ok: true }),
    ),
  };
}

type ClientStub = ReturnType<typeof makeClient>;

type Render = (typeof import("ink"))["render"];
const instances: Array<ReturnType<Render>> = [];
let inkRender: Render;
let ReviewerPanel: (typeof import("../ReviewerPanel.js"))["ReviewerPanel"];

beforeAll(async () => {
  inkRender = (await import("ink")).render;
  ({ ReviewerPanel } = await import("../ReviewerPanel.js"));
});

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
});

async function mount(
  kind: ReviewerKind,
  stub: ClientStub,
  // `null` = no session. NOT `undefined`: a default parameter applies to
  // an explicitly-passed undefined, so the "no session yet" case would
  // have silently mounted with "s-1" and asserted nothing.
  sessionId: string | null = "s-1",
) {
  const stdout = new TestStdout();
  const stdin = new FakeStdin();
  const onClose = vi.fn();
  const instance = inkRender(
    <KeypressProvider>
      <ReviewerPanel
        kind={kind}
        open
        onClose={onClose}
        client={stub as unknown as RPCClient}
        sessionId={sessionId ?? undefined}
      />
    </KeypressProvider>,
    {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  instances.push(instance);
  await settle(40);
  return { stdout, stdin, onClose };
}

describe("ReviewerPanel resolves its own position", () => {
  it("shows the built-in default when nothing is set", async () => {
    const { stdout } = await mount("approvals", makeClient());
    const frame = stdout.lastFrame();
    expect(frame).toContain("Ask me first");
    expect(frame).toContain("default");
  });

  it("reflects a config value rather than assuming the default", async () => {
    // The case a hardcoded initial state gets wrong: config says auto,
    // so the guardian is answering, and rendering "Ask me first" would
    // be a switch lying about its own position.
    const { stdout } = await mount(
      "approvals",
      makeClient({
        configFields: [
          { key: "approvals.reviewer", value: "auto", is_set: true },
        ],
      }),
    );
    const frame = stdout.lastFrame();
    expect(frame).toContain("Approve for me");
    expect(frame).toContain("from your configuration");
  });

  it("lets a session override beat the config", async () => {
    const { stdout } = await mount(
      "approvals",
      makeClient({
        approvalsOverride: "user",
        configFields: [
          { key: "approvals.reviewer", value: "auto", is_set: true },
        ],
      }),
    );
    expect(stdout.lastFrame()).toContain("set for this conversation");
  });

  it("defaults task checks to ON, matching the server", async () => {
    // workflow.completion_reviewer defaults to guardian, so a panel
    // that showed "Checks off" would misreport reviews already running.
    const { stdout } = await mount("checks", makeClient());
    const frame = stdout.lastFrame();
    expect(frame).toContain("Task checks on");
    expect(frame).toContain("default");
  });

  it("still renders when get_config is unavailable", async () => {
    const stub = makeClient();
    stub.getConfig.mockRejectedValueOnce(new Error("not granted"));
    const { stdout } = await mount("approvals", stub);
    expect(stdout.lastFrame()).toContain("Ask me first");
  });
});

describe("ReviewerPanel writes", () => {
  it("sets the approvals reviewer and closes", async () => {
    const stub = makeClient();
    const { stdin, onClose } = await mount("approvals", stub);
    stdin.press(KEY.down);
    await settle();
    stdin.press(KEY.enter);
    await settle(40);
    expect(stub.setSessionApprovals).toHaveBeenCalledWith("s-1", "auto");
    expect(onClose).toHaveBeenCalled();
  });

  it("sets the workflow reviewer through its own RPC", async () => {
    const stub = makeClient();
    const { stdin } = await mount("checks", stub);
    stdin.press(KEY.down);
    await settle();
    stdin.press(KEY.enter);
    await settle(40);
    expect(stub.setSessionWorkflow).toHaveBeenCalledWith("s-1", "off");
    expect(stub.setSessionApprovals).not.toHaveBeenCalled();
  });

  it("surfaces a server rejection instead of showing the switch moved", async () => {
    const stub = makeClient();
    stub.setSessionApprovals.mockResolvedValueOnce({
      ok: false,
      reason: "unknown reviewer",
    });
    const { stdin, stdout, onClose } = await mount("approvals", stub);
    stdin.press(KEY.down);
    await settle();
    stdin.press(KEY.enter);
    await settle(40);
    expect(stdout.lastFrame()).toContain("unknown reviewer");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("explains that the switch needs a session before it can be set", async () => {
    const stub = makeClient();
    const { stdin, stdout } = await mount("approvals", stub, null);
    stdin.press(KEY.down);
    await settle();
    stdin.press(KEY.enter);
    await settle(40);
    expect(stub.setSessionApprovals).not.toHaveBeenCalled();
    expect(stdout.lastFrame()).toContain("send a message first");
  });

  it("closes on escape without writing", async () => {
    const stub = makeClient();
    const { stdin, onClose } = await mount("approvals", stub);
    stdin.press(KEY.escape);
    await settle();
    expect(onClose).toHaveBeenCalled();
    expect(stub.setSessionApprovals).not.toHaveBeenCalled();
  });
});
