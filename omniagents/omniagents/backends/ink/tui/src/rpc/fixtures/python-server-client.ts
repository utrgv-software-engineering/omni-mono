/**
 * Invoked by tests/integration/test_ink_rpc.py against its real Python
 * server: the TUI's connection and conversation layer (@omniagents/client,
 * shared with the desktop and web UI) across lost replies and reconnects.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { respondToApproval } from "@omniagents/client/rpc/approval-response";
import { RPCClient } from "@omniagents/client/rpc/client";
import { FsClient } from "@omniagents/client/rpc/fs";
import { GitClient } from "@omniagents/client/rpc/git";
import type { ConversationSession } from "@omniagents/client/session/conversation-session";
import { getSessionRegistry } from "@omniagents/client/session/session-registry";
import { uuidv4 } from "@omniagents/client/util/uuid";
import { defaultEnvironmentId } from "../host.js";
import { CLIENT_IDENTITY } from "../../client-identity.js";

const [base, scenario, reconnect] = process.argv.slice(2) as [
  string,
  string,
  string,
];
const url = base.replace("http:", "ws:") + "/ws";
const client = new RPCClient(url, CLIENT_IDENTITY);
const registry = getSessionRegistry(client);
const releaseSessions = registry.retain();

/** Completed handshakes, counted so a test can wait for the next one. */
let connections = 0;
client.onConnectionState((state) => {
  if (state === "connected") connections++;
});

async function until(
  check: () => boolean | Promise<boolean>,
  describe?: () => string,
): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (!(await check())) {
    assert.ok(
      Date.now() < deadline,
      `Timed out waiting for scenario ${scenario}${describe ? `: ${describe()}` : ""}`,
    );
    await delay(10);
  }
}

type ServerState = {
  started: boolean;
  active: boolean;
  runs: number;
  requests: Array<{ method: string; params?: Record<string, unknown> }>;
};

async function state(): Promise<ServerState> {
  return (await (await fetch(base + "/test/state")).json()) as ServerState;
}

const requestsOf = async (method: string) =>
  (await state()).requests.filter((request) => request.method === method);

async function post(path: string) {
  assert.equal((await fetch(base + path, { method: "POST" })).status, 200);
}

/** The server answers the next `method` call, then drops the connection
 * before the reply leaves: the call happened, its reply is lost. */
const lose = (method: string) => post("/test/lose/" + method);

/** Every live connection drops; resolves once the client is back. */
async function dropConnection(): Promise<void> {
  const before = connections;
  await post("/test/drop");
  await until(() => connections > before);
}

/** A conversation the way the TUI holds one, with a count of its loads so a
 * test can wait for the reload a reconnect triggers. */
function open(id: string) {
  const session = registry.get(id);
  const release = registry.retainSession(id);
  let loads = 0;
  let ready = session.actor.getSnapshot().matches("ready");
  const subscription = session.actor.subscribe((snapshot) => {
    const next = snapshot.matches("ready");
    if (next && !ready) loads++;
    ready = next;
  });
  return {
    session,
    loads: () => loads,
    close() {
      subscription.unsubscribe();
      release();
    },
  };
}

const pendingApprovals = (session: ConversationSession) => [
  ...session.actor.getSnapshot().context.pendingApprovals.values(),
];
const isIdle = (session: ConversationSession) =>
  session.actor.getSnapshot().matches({ ready: "idle" });
const serverIdle = async () => !(await state()).active;

async function main(): Promise<void> {
  try {
    await client.connect();
    if (scenario === "screens") {
      const { runScreenSoak } = await import("./screen-soak.js");
      await runScreenSoak(client, base, { until, open, listenerCounts });
    } else if (scenario === "workspace") {
      await workspace();
    } else if (scenario === "soak") {
      await soak();
    } else if (scenario.startsWith("lost_") || scenario === "approval_other") {
      await ambiguousOutcome();
    } else {
      await restoreAcrossReconnect();
    }
  } finally {
    releaseSessions();
    client.dispose();
  }
}

/** A live run, and its one pending question, survive a reconnect intact and
 * are answered once. */
async function restoreAcrossReconnect(): Promise<void> {
  const conversation = open(uuidv4());
  const { session } = conversation;
  await session.load();
  await session.send(scenario);
  await until(async () => (await state()).started);
  const live = (): Array<Record<string, unknown>> =>
    scenario === "elicitation"
      ? (client.elicitations.list(session.id) as never)
      : (pendingApprovals(session) as never);
  if (scenario !== "completed") await until(() => live().length === 1);
  const original = live()[0];
  assert.equal(isIdle(session), false);

  const loadsBefore = conversation.loads();
  if (reconnect === "automatic") {
    await dropConnection();
  } else {
    client.disconnect();
    if (scenario === "completed") {
      await post("/test/finish");
      await until(serverIdle);
    }
    await client.connect();
  }
  // The reconnect re-reads the conversation from the server.
  await until(() => conversation.loads() > loadsBefore);

  if (scenario === "completed") {
    await post("/test/finish");
  } else {
    assert.equal(live().length, 1, "Restore exactly one live question");
    const restored = live()[0]!;
    if (scenario === "approval") {
      assert.ok(
        original?.metadata,
        "The prompt must carry its display metadata",
      );
    }
    if (scenario === "elicitation") assert.ok(original?.expiresAt);
    for (const field of Object.keys(original!)) {
      assert.deepEqual(restored[field], original![field], field);
    }
    if (scenario === "elicitation") {
      await client.elicitations.respond(String(restored.elicitationId), {
        action: "accept",
        value: { text: "example.txt" },
      });
    } else {
      const requestId = String(restored.request_id);
      await respondToApproval(
        client,
        requestId,
        "yes",
        scenario === "mcp" ? "mcp" : "function",
      );
      session.receive({
        type: "APPROVAL_DECIDED",
        request_id: requestId,
        value: "yes",
      });
    }
  }
  await until(serverIdle);
  await until(
    () => isIdle(session),
    () => JSON.stringify(session.actor.getSnapshot().value),
  );
  await session.load({ force: true });
  assert.equal(live().length, 0, "Answered questions stay answered");
  assert.equal(isIdle(session), true);
  conversation.close();
}

/** A reply lost in transit: the client recovers what the server did, and
 * never does it twice. */
async function ambiguousOutcome(): Promise<void> {
  const conversation = open(uuidv4());
  const { session } = conversation;
  await session.load();

  if (scenario === "lost_start") {
    await lose("start_run");
    const sent = await session.send("completed");
    assert.ok(sent?.runId, "The accepted run is recovered from its receipt");
    const starts = await requestsOf("start_run");
    assert.equal(starts.length, 1, "The run is never started twice");
    assert.ok(starts[0]!.params?.submission_id);
    assert.equal((await state()).runs, 1);
    await post("/test/finish");
    await until(() => isIdle(session));
  } else if (scenario === "lost_queue") {
    await session.send("completed");
    await until(async () => (await state()).started);
    await lose("enqueue_message");
    assert.deepEqual(await session.send("next"), { runId: "" });
    const queue = await client.listQueue(session.id);
    assert.equal(queue.items.length, 1, "The queued message is recovered");
    assert.equal((await requestsOf("enqueue_message")).length, 1);
    assert.equal((await state()).runs, 1);
  } else {
    await session.send("approval");
    await until(() => pendingApprovals(session).length === 1);
    const requestId = pendingApprovals(session)[0]!.request_id;
    if (scenario === "lost_approval") {
      const loadsBefore = conversation.loads();
      await lose("tool_approval_response");
      await assert.rejects(respondToApproval(client, requestId, "yes"));
      await until(() => conversation.loads() > loadsBefore);
      assert.equal((await requestsOf("tool_approval_response")).length, 1);
    } else {
      const other = new RPCClient(url, CLIENT_IDENTITY);
      try {
        await other.connect();
        assert.equal(
          await other.toolApprovalResponse(requestId, "approve"),
          true,
        );
        await until(() => pendingApprovals(session).length === 0);
        await assert.rejects(respondToApproval(client, requestId, "no"));
      } finally {
        other.dispose();
      }
    }
    await session.load({ force: true });
    assert.equal(
      pendingApprovals(session).length,
      0,
      "An answered approval never becomes actionable again",
    );
  }
  conversation.close();
}

/** The client's event subscriptions, by event: a reconnect must not grow them. */
function listenerCounts(): Record<string, number> {
  const listeners = (
    client as unknown as { listeners: Map<string, Set<unknown>> }
  ).listeners;
  return Object.fromEntries(
    [...listeners]
      .filter(([, handlers]) => handlers.size > 0)
      .map(([event, handlers]) => [event, handlers.size]),
  );
}

/** Many reconnects while a run is live and other conversations come and go:
 * cancelled queue entries never run and nothing accumulates. */
async function soak(): Promise<void> {
  const main = open(uuidv4());
  await main.session.load();
  await main.session.send("completed");
  await until(async () => (await state()).started);
  let baseline: Record<string, number> | null = null;
  for (let cycle = 0; cycle < 24; cycle++) {
    const other = cycle % 3 === 0 ? null : open(`soak-${cycle % 3}`);
    await other?.session.load();
    await main.session.send(`message-${cycle}`);
    const queued = () => main.session.panels.state.get().queuedMessages;
    await until(() => queued().length === 1);
    assert.equal(
      (await client.cancelQueuedMessage(main.session.id, queued()[0]!.id)).ok,
      true,
    );
    const loadsBefore = main.loads();
    await dropConnection();
    await until(() => main.loads() > loadsBefore);
    assert.equal(isIdle(main.session), false, "The run is still live");
    assert.equal((await client.listQueue(main.session.id)).items.length, 0);
    other?.close();
    // Every conversation this soak opens exists after the third cycle.
    if (cycle === 2) baseline = listenerCounts();
    if (baseline) {
      assert.deepEqual(
        listenerCounts(),
        baseline,
        `Listeners grew in cycle ${cycle}`,
      );
    }
  }
  await post("/test/finish");
  await until(serverIdle);
  assert.equal(
    (await state()).runs,
    1,
    "Cancelled queue entries never run across reconnects",
  );
  main.close();
}

/** Writes and patches are never replayed: a lost reply is resolved by
 * reading the workspace back. */
async function workspace(): Promise<void> {
  const env = defaultEnvironmentId(client);
  assert.ok(env);
  const fs = new FsClient(client);
  const git = new GitClient(client, env);
  // Hostile but legal: Windows file names cannot contain "*".
  const path =
    process.platform === "win32"
      ? "雪 space '$(touch injected);.txt"
      : "雪 space '$(touch injected);*.txt";
  const sha = (text: string) => createHash("sha256").update(text).digest("hex");
  const bytes = (text: string) => new TextEncoder().encode(text);
  const read = async () =>
    new TextDecoder().decode((await fs.downloadBytes(env, path)).bytes);
  try {
    await fs.uploadBytes(env, path, bytes("original"), { overwrite: true });
    assert.equal(await read(), "original");
    assert.ok(
      (await fs.list(env, ".")).entries.some((entry) => entry.path === path),
    );

    // A chunk's lost reply restarts the uncommitted transfer from scratch.
    await lose("fs_upload_chunk");
    await fs.uploadBytes(env, path, bytes("partial"), {
      overwrite: true,
      expectedSha256: sha("original"),
    });
    assert.equal(await read(), "partial");

    // A commit's lost reply is confirmed by its digest, not committed again.
    await lose("fs_upload_commit");
    assert.deepEqual(
      await fs.uploadBytes(env, path, bytes("committed"), {
        overwrite: true,
        expectedSha256: sha("partial"),
      }),
      { path, size: 9, sha256: sha("committed") },
    );
    assert.equal(await read(), "committed");
    assert.equal(
      (await requestsOf("fs_upload_commit")).length,
      3,
      "A committed write is never replayed",
    );

    const repo = (await git.listRepositories()).repositories[0]!.repo;
    const diff = await git.diff(repo, {
      paths: ["patch.txt"],
      mode: "worktree",
    });
    const hunk = diff.files[0]!.hunks[0]!.hunk_id;
    const before = connections;
    await lose("git_stage");
    await assert.rejects(
      git.stage(repo, { hunks: [{ path: "patch.txt", hunk_id: hunk }] }),
    );
    await until(() => connections > before);
    const staged = await git.diff(repo, {
      paths: ["patch.txt"],
      mode: "staged",
    });
    assert.equal(staged.files.length, 1);
    assert.equal(staged.files[0]!.hunks.length, 1);
    assert.equal(
      (await requestsOf("git_stage")).length,
      1,
      "A patch is never replayed",
    );
    assert.ok(
      !(await fs.list(env, ".")).entries.some(
        (entry) => entry.path === "injected",
      ),
    );
  } finally {
    fs.dispose();
  }
}

await main();
