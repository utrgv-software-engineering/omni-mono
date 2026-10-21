import React from "react";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { RPCClient } from "../../rpc/client.js";
import type { ReviewerKind } from "../ReviewerPanel.js";
import { cleanup, render } from "ink-testing-library";

const plain = (frame?: string) => stripAnsi(frame ?? "");

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

let ReviewerPanel: (typeof import("../ReviewerPanel.js"))["ReviewerPanel"];

beforeAll(async () => {
  ({ ReviewerPanel } = await import("../ReviewerPanel.js"));
});

afterEach(() => {
  cleanup();
});

async function mount(
  kind: ReviewerKind,
  stub: ClientStub,
  // `null` = no session. NOT `undefined`: a default parameter applies to
  // an explicitly-passed undefined, so the "no session yet" case would
  // have silently mounted with "s-1" and asserted nothing.
  sessionId: string | null = "s-1",
) {
  const onClose = vi.fn();
  const instance = render(
    <KeypressProvider>
      <ReviewerPanel
        kind={kind}
        open
        onClose={onClose}
        client={stub as unknown as RPCClient}
        sessionId={sessionId ?? undefined}
      />
    </KeypressProvider>,
  );
  await settle(40);
  return { ...instance, onClose };
}

describe("ReviewerPanel resolves its own position", () => {
  it("shows the built-in default when nothing is set", async () => {
    const { lastFrame } = await mount("approvals", makeClient());
    const frame = plain(lastFrame());
    expect(frame).toContain("Ask me first");
    expect(frame).toContain("default");
  });

  it("reflects a config value rather than assuming the default", async () => {
    // The case a hardcoded initial state gets wrong: config says auto,
    // so the guardian is answering, and rendering "Ask me first" would
    // be a switch lying about its own position.
    const { lastFrame } = await mount(
      "approvals",
      makeClient({
        configFields: [
          { key: "approvals.reviewer", value: "auto", is_set: true },
        ],
      }),
    );
    const frame = plain(lastFrame());
    expect(frame).toContain("Approve for me");
    expect(frame).toContain("from your configuration");
  });

  it("lets a session override beat the config", async () => {
    const { lastFrame } = await mount(
      "approvals",
      makeClient({
        approvalsOverride: "user",
        configFields: [
          { key: "approvals.reviewer", value: "auto", is_set: true },
        ],
      }),
    );
    expect(plain(lastFrame())).toContain("set for this conversation");
  });

  it("defaults task checks to ON, matching the server", async () => {
    // workflow.completion_reviewer defaults to guardian, so a panel
    // that showed "Checks off" would misreport reviews already running.
    const { lastFrame } = await mount("checks", makeClient());
    const frame = plain(lastFrame());
    expect(frame).toContain("Task checks on");
    expect(frame).toContain("default");
  });

  it("still renders when get_config is unavailable", async () => {
    const stub = makeClient();
    stub.getConfig.mockRejectedValueOnce(new Error("not granted"));
    const { lastFrame } = await mount("approvals", stub);
    expect(plain(lastFrame())).toContain("Ask me first");
  });
});

describe("ReviewerPanel writes", () => {
  it("sets the approvals reviewer and closes", async () => {
    const stub = makeClient();
    const { stdin, onClose } = await mount("approvals", stub);
    stdin.write(KEY.down);
    await settle();
    stdin.write(KEY.enter);
    await settle(40);
    expect(stub.setSessionApprovals).toHaveBeenCalledWith("s-1", "auto");
    expect(onClose).toHaveBeenCalled();
  });

  it("sets the workflow reviewer through its own RPC", async () => {
    const stub = makeClient();
    const { stdin } = await mount("checks", stub);
    stdin.write(KEY.down);
    await settle();
    stdin.write(KEY.enter);
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
    const { stdin, lastFrame, onClose } = await mount("approvals", stub);
    stdin.write(KEY.down);
    await settle();
    stdin.write(KEY.enter);
    await settle(40);
    expect(plain(lastFrame())).toContain("unknown reviewer");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("explains that the switch needs a session before it can be set", async () => {
    const stub = makeClient();
    const { stdin, lastFrame } = await mount("approvals", stub, null);
    stdin.write(KEY.down);
    await settle();
    stdin.write(KEY.enter);
    await settle(40);
    expect(stub.setSessionApprovals).not.toHaveBeenCalled();
    expect(plain(lastFrame())).toContain("send a message first");
  });

  it("closes on escape without writing", async () => {
    const stub = makeClient();
    const { stdin, onClose } = await mount("approvals", stub);
    stdin.write(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(onClose).toHaveBeenCalled();
    expect(stub.setSessionApprovals).not.toHaveBeenCalled();
  });
});
