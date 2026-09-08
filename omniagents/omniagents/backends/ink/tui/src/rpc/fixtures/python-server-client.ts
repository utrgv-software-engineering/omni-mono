import { createHash } from "node:crypto";
import { downloadFile, uploadFile } from "../file-transfer.js";
/** Invoked by tests/integration/test_ink_rpc.py against its real Python server. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { RPCClient, type SessionSnapshot } from "../client.js";
import { DEFAULT_LIFECYCLE_POLICY } from "../lifecycle.js";

const [base, scenario, reconnect] = process.argv.slice(2);
const client = new RPCClient({
  url: base.replace("http:", "ws:") + "/ws",
  policy:
    scenario === "soak" || scenario === "workspace" || scenario === "screens"
      ? {
          ...DEFAULT_LIFECYCLE_POLICY,
          reconnectInitialDelayMs: 10,
          reconnectJitter: 0,
        }
      : undefined,
});
const errors: unknown[] = [];
client.on("error", (error) => errors.push(error));
const approvals: Record<string, unknown>[] = [];
const method =
  scenario === "mcp"
    ? "mcp_approval_requested"
    : scenario === "elicitation"
      ? "elicitation_requested"
      : "tool_approval_requested";
client.on(method, (params) => approvals.push(params));

async function until(check: () => boolean | Promise<boolean>): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (!(await check())) {
    assert.ok(
      Date.now() < deadline,
      "Timed out waiting for scenario " + scenario,
    );
    await delay(10);
  }
}

async function main(): Promise<void> {
  try {
    await client.connect();
    if (scenario === "screens") {
      const { runScreenSoak } = await import("./screen-soak.js");
      await runScreenSoak(client, base);
      return;
    }
    if (scenario === "workspace") {
      await workspace();
      return;
    }
    if (scenario === "soak") {
      await soak();
      return;
    }
    if (scenario.startsWith("lost_") || scenario === "approval_other") {
      await ambiguousOutcome();
      return;
    }
    const { session_id, run_id } = await client.startRun(scenario, {
      mode: "none",
    });
    await until(async () => (await state()).started);
    if (scenario !== "completed") await until(() => approvals.length === 1);
    let snapshot: SessionSnapshot | undefined;
    await client.synchronizeSession(session_id, (value) => {
      snapshot = value;
    });
    assert.equal(snapshot?.run_active, true);
    assert.equal(snapshot?.active_run_id, run_id);
    const original = approvals[0];
    approvals.length = 0;
    if (reconnect === "automatic") {
      let restored = false;
      client.once("rpc_ready", () => {
        void client
          .synchronizeSession(session_id, (value) => {
            snapshot = value;
          })
          .then(() => {
            restored = true;
          })
          .catch((error) => errors.push(error));
      });
      const response = await fetch(base + "/test/drop", { method: "POST" });
      assert.equal(response.status, 200);
      await until(() => restored);
    } else {
      client.disconnect();
      if (scenario === "completed") {
        await fetch(base + "/test/finish", { method: "POST" });
        await until(async () => !(await state()).active);
      }
      await client.connect();
      await client.synchronizeSession(session_id, (value) => {
        snapshot = value;
      });
    }
    if (scenario !== "completed") {
      assert.equal(approvals.length, 1, "Restore exactly one live approval");
      if (scenario === "approval")
        assert.ok(
          original.metadata,
          "The live prompt must have rich display metadata",
        );
      if (scenario === "elicitation") assert.ok(original.expires_at);
      for (const field of Object.keys(original).filter(
        (key) => !["seq", "stream_id"].includes(key),
      )) {
        assert.deepEqual(approvals[0][field], original[field], field);
      }
      if (scenario === "approval") {
        assert.equal(
          await client.toolApprovalResponse(
            String(approvals[0].call_id),
            "approve",
          ),
          true,
        );
      } else if (scenario === "mcp") {
        assert.equal(
          await client.mcpApprovalResponse(
            String(approvals[0].request_id),
            "approve",
          ),
          true,
        );
      } else {
        await client.elicitationResponse(
          String(approvals[0].elicitation_id),
          "accept",
          { text: "example.txt" },
        );
      }
      await until(async () => !(await state()).active);
      approvals.length = 0;
      await client.synchronizeSession(session_id, (value) => {
        snapshot = value;
      });
      assert.equal(
        approvals.length,
        0,
        "Resolved approvals must stay historical",
      );
    }
    assert.equal(snapshot?.run_active, false);
    assert.equal(snapshot?.active_run_id, null);
    assert.deepEqual(errors, []);
  } finally {
    client.disconnect();
  }
}

async function ambiguousOutcome(): Promise<void> {
  const lose = async (method: string) => {
    assert.equal(
      (await fetch(base + "/test/lose/" + method, { method: "POST" })).status,
      200,
    );
  };
  if (scenario.startsWith("lost_start")) await lose("start_run");
  if (scenario === "lost_start_switch")
    client.once("disconnected", () => client.setActiveSession("other-session"));
  const approvalCase =
    scenario === "lost_approval" || scenario === "approval_other";
  const run = await client.startRun(approvalCase ? "approval" : "completed", {
    mode: "none",
  });
  await until(async () => (await state()).started);
  if (scenario.startsWith("lost_start")) {
    assert.equal((await state()).runs, 1);
    const starts = (await state()).requests.filter(
      (request) => request.method === "start_run",
    );
    assert.equal(
      starts.length,
      1,
      "Recover the accepted result from its receipt without dispatching again",
    );
    assert.ok(starts[0].params?.submission_id);
    if (scenario === "lost_start_switch") {
      const ended: unknown[] = [];
      client.on("run_end", (event) => ended.push(event));
      await fetch(base + "/test/finish", { method: "POST" });
      await until(async () => !(await state()).active);
      assert.equal(
        ended.length,
        0,
        "Recovery must not reselect the abandoned session",
      );
    }
  } else if (scenario === "lost_queue") {
    await lose("enqueue_message");
    const queued = await client.enqueueMessage(run.session_id, "next", {
      triggerRun: true,
    });
    assert.equal(queued.ok, true);
    const queue = await client.listQueue(run.session_id);
    assert.equal(queue.items.length, 1);
    assert.equal(queue.items[0].id, queued.id);
    assert.equal((await state()).runs, 1);
    assert.equal(
      (await state()).requests.filter(
        (request) => request.method === "enqueue_message",
      ).length,
      1,
    );
  } else {
    await until(() => approvals.length === 1);
    const id = String(approvals[0].call_id);
    const resolved: string[] = [];
    client.on("tool_approval_resolved", (params) =>
      resolved.push(params.call_id),
    );
    if (scenario === "lost_approval") {
      await lose("tool_approval_response");
      await client.toolApprovalResponse(id, "approve").catch(() => {});
      await until(() => client.initializeResult !== null);
    } else {
      const other = new RPCClient({
        url: base.replace("http:", "ws:") + "/ws",
      });
      try {
        await other.connect();
        assert.equal(await other.toolApprovalResponse(id, "approve"), true);
        await until(() => resolved.includes(id));
        await assert.rejects(client.toolApprovalResponse(id, "reject"));
      } finally {
        other.disconnect();
      }
    }
    approvals.length = 0;
    await client.synchronizeSession(run.session_id, () => {});
    assert.equal(
      approvals.length,
      0,
      "A resolved approval must not become actionable again",
    );
  }
}

async function soak(): Promise<void> {
  const run = await client.startRun("completed", { mode: "none" });
  await until(async () => (await state()).started);
  const listenerCounts = () =>
    Object.fromEntries(
      client
        .eventNames()
        .map((name) => [String(name), client.listenerCount(name)]),
    );
  const baseline = listenerCounts();
  for (let cycle = 0; cycle < 24; cycle++) {
    const session = cycle % 3 === 0 ? run.session_id : `soak-${cycle % 3}`;
    client.setActiveSession(session);
    const queued = await client.enqueueMessage(
      run.session_id,
      `message-${cycle}`,
      { triggerRun: true },
    );
    assert.equal(queued.ok, true);
    assert.equal(
      (await client.cancelQueuedMessage(run.session_id, queued.id!)).ok,
      true,
    );
    let ready = false;
    client.once("rpc_ready", () => {
      ready = true;
    });
    assert.equal(
      (await fetch(base + "/test/drop", { method: "POST" })).status,
      200,
    );
    await until(() => ready);
    let snapshot: SessionSnapshot | undefined;
    await client.synchronizeSession(session, (value) => {
      snapshot = value;
    });
    assert.equal(snapshot?.run_active, session === run.session_id);
    assert.equal((await client.listQueue(run.session_id)).items.length, 0);
    assert.deepEqual(
      listenerCounts(),
      baseline,
      `Listeners grew in cycle ${cycle}`,
    );
  }
  await fetch(base + "/test/finish", { method: "POST" });
  await until(async () => !(await state()).active);
  assert.equal(
    (await state()).runs,
    1,
    "Cancelled queue entries must not execute during reconnects",
  );
  assert.deepEqual(errors, []);
}

async function state(): Promise<{
  started: boolean;
  active: boolean;
  runs: number;
  requests: Array<{ method: string; params?: Record<string, unknown> }>;
}> {
  return (await (await fetch(base + "/test/state")).json()) as {
    started: boolean;
    active: boolean;
    runs: number;
    requests: Array<{ method: string; params?: Record<string, unknown> }>;
  };
}

await main();

async function workspace(): Promise<void> {
  const env = client.defaultEnvironmentId();
  assert.ok(env);
  const path = "雪 space '$(touch injected);*.txt";
  const sha = (text: string) => createHash("sha256").update(text).digest("hex");
  async function upload(text: string, expected?: string) {
    const bytes = Buffer.from(text);
    const open = await client.fsUploadOpen(env!, path, bytes.length, {
      overwrite: true,
      sha256: sha(text),
      expectedSha256: expected,
    });
    await client.fsUploadChunk(
      env!,
      open.transfer_id,
      0,
      bytes.toString("base64"),
    );
    return open;
  }
  const initial = await upload("original");
  await client.fsUploadCommit(env, initial.transfer_id);
  assert.equal(
    (await downloadFile(client, env, path, 1024)).bytes.toString(),
    "original",
  );
  assert.ok(
    (await client.fsList(env, ".")).entries.some(
      (entry) => entry.path === path,
    ),
  );
  async function lose(method: string, operation: () => Promise<unknown>) {
    await fetch(base + "/test/lose/" + method, { method: "POST" });
    const ready = new Promise<void>((resolve) =>
      client.once("rpc_ready", () => resolve()),
    );
    await assert.rejects(operation);
    await ready;
  }
  const interrupted = await client.fsUploadOpen(env, path, 7, {
    overwrite: true,
    sha256: sha("partial"),
    expectedSha256: sha("original"),
  });
  await lose("fs_upload_chunk", () =>
    client.fsUploadChunk(
      env,
      interrupted.transfer_id,
      0,
      Buffer.from("partial").toString("base64"),
    ),
  );
  assert.equal(
    (await downloadFile(client, env, path, 1024)).bytes.toString(),
    "original",
  );
  await client.fsUploadAbort(env, interrupted.transfer_id).catch(() => {});
  await fetch(base + "/test/lose/fs_upload_commit", { method: "POST" });
  assert.equal(
    await uploadFile(
      client,
      env,
      path,
      Buffer.from("committed"),
      sha("original"),
    ),
    sha("committed"),
  );
  assert.equal(
    (await downloadFile(client, env, path, 1024)).bytes.toString(),
    "committed",
  );
  const repo = (await client.gitListRepositories(env)).repositories[0].repo;
  const diff = await client.gitDiff(env, repo, {
    paths: ["patch.txt"],
    mode: "worktree",
  });
  const hunk = diff.files[0].hunks[0].hunk_id;
  await lose("git_stage", () =>
    client.gitStage(env, repo, {
      hunks: [{ path: "patch.txt", hunk_id: hunk }],
    }),
  );
  const staged = await client.gitDiff(env, repo, {
    paths: ["patch.txt"],
    mode: "staged",
  });
  assert.equal(staged.files.length, 1);
  assert.equal(staged.files[0].hunks.length, 1);
  const requests = (await state()).requests;
  assert.equal(
    requests.filter((r) => r.method === "git_stage").length,
    1,
    "Do not replay a patch on reconnect",
  );
  assert.equal(
    requests.filter((r) => r.method === "fs_upload_commit").length,
    2,
    "Do not replay a committed write",
  );
  assert.ok(
    !(await client.fsList(env, ".")).entries.some(
      (entry) => entry.path === "injected",
    ),
  );
}
