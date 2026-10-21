/** Real Python RPC + real Ink screens, invoked by test_ink_rpc.py. */
import React, { useEffect, useState } from "react";
import assert from "node:assert/strict";
import { Writable } from "node:stream";
import { EventEmitter } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import { Box, Text, render } from "ink";
import xterm from "@xterm/headless";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { MouseProvider } from "../../contexts/MouseContext.js";
import { ScrollProvider } from "../../contexts/ScrollProvider.js";
import { ReviewScreen } from "../../components/ReviewScreen.js";
import { AgentsScreen } from "../../components/AgentsScreen.js";
import { SessionManager } from "../../components/SessionManager.js";
import { ApprovalDialog } from "../../components/ApprovalDialog.js";
import {
  SessionComposer,
  ComposerDrafts,
} from "../../components/SessionComposer.js";
import { MessageListOptimized } from "../../components/MessageListOptimized.js";
import { Scrollable } from "../../components/Scrollable.js";
import { useMessagesOptimized } from "../../hooks/useMessagesNew.js";
import { useAppStore, useApproval, useMessages } from "../../store/appStore.js";
import type { RPCClient } from "../client.js";

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
  press(text: string) {
    this.queue.push(text);
    this.emit("readable");
  }
}
async function until(check: () => boolean, describe?: () => string) {
  const deadline = Date.now() + 10_000;
  while (!check()) {
    if (Date.now() >= deadline)
      assert.fail(`screen soak timed out${describe ? `: ${describe()}` : ""}`);
    await delay(10);
  }
}

export async function runScreenSoak(client: RPCClient, base: string) {
  const first = await client.startRun("approval", { mode: "none" });
  const second = await client.startRun("completed", { mode: "none" });
  const sessions = [first.session_id, second.session_id];
  const drafts = new ComposerDrafts();
  const typed = ["", ""];
  const stdin = new Input();
  const terminal = new xterm.Terminal({
    cols: 100,
    rows: 40,
    allowProposedApi: true,
    convertEol: true,
  });
  const stdout = new Writable({
    write(chunk, _encoding, done) {
      terminal.write(chunk, done);
    },
  });
  Object.assign(stdout, { columns: 100, rows: 40, isTTY: true });
  const screen = () =>
    Array.from(
      { length: terminal.rows },
      (_, index) =>
        terminal.buffer.active
          .getLine(terminal.buffer.active.baseY + index)
          ?.translateToString(true) ?? "",
    ).join("\n");
  const listeners = () =>
    Object.fromEntries(
      client
        .eventNames()
        .map((name) => [String(name), client.listenerCount(name)]),
    );
  const originalListeners = listeners();
  let recovered = 0;
  function Root({ tab, session }: { tab: string; session: string }) {
    const [ready, setReady] = useState(true);
    useEffect(() => {
      const online = () => {
        recovered++;
        setReady(true);
      };
      const offline = () => setReady(false);
      client.on("rpc_ready", online);
      client.on("disconnected", offline);
      return () => {
        client.off("rpc_ready", online);
        client.off("disconnected", offline);
      };
    }, []);
    const api = useMessagesOptimized({
      client,
      sessionId: session,
      rpcReady: ready,
    });
    const approval = useApproval();
    const messages = useMessages();
    return (
      <Box flexDirection="column" height={38}>
        <Text>{`SCREEN_${tab} SESSION_${sessions.indexOf(session)}`}</Text>
        {tab === "review" ? (
          <ReviewScreen active height={35} client={client} onExit={() => {}} />
        ) : tab === "agents" ? (
          <AgentsScreen active height={35} onExit={() => {}} />
        ) : tab === "sessions" ? (
          <SessionManager client={client} onOpen={() => {}} onNew={() => {}} />
        ) : tab === "approval" ? (
          approval ? (
            <ApprovalDialog
              key={approval.call_id}
              tool={approval.tool}
              arguments={approval.arguments}
              metadata={approval.metadata}
              onApprove={(value) => {
                void api.approveToolCall(
                  value !== "no",
                  value === "always",
                  approval.call_id,
                );
              }}
            />
          ) : (
            <Text>No pending approval</Text>
          )
        ) : (
          <>
            <Scrollable
              hasFocus
              scrollToBottom
              maxHeight={25}
              contentVersion={messages.length}
            >
              <MessageListOptimized />
            </Scrollable>
            <SessionComposer
              drafts={drafts}
              sessionId={session}
              onSubmit={() => false}
              maxHeight={4}
            />
          </>
        )}
      </Box>
    );
  }
  const view = (tab: string, session: string) => (
    <KeypressProvider>
      <MouseProvider>
        <ScrollProvider>
          <Root tab={tab} session={session} />
        </ScrollProvider>
      </MouseProvider>
    </KeypressProvider>
  );
  useAppStore.getState().actions.setSessionId(first.session_id);
  const instance = render(view("approval", first.session_id), {
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream,
    stderr: stdout as unknown as NodeJS.WriteStream,
    exitOnCtrlC: false,
    patchConsole: false,
  });
  try {
    await until(() => !!useAppStore.getState().approval);
    const baseline = listeners();
    const tabs = ["review", "agents", "sessions", "chat", "approval"];
    for (let i = 0; i < 25; i++) {
      const tab = tabs[i % tabs.length];
      const selected = Math.floor(i / tabs.length) % 2;
      const session = sessions[selected];
      useAppStore.getState().actions.setSessionId(session);
      Object.assign(stdout, { columns: i % 2 ? 80 : 100 });
      terminal.resize(i % 2 ? 80 : 100, 40);
      stdout.emit("resize");
      instance.rerender(view(tab, session));
      await until(
        () => screen().includes(`SCREEN_${tab} SESSION_${selected}`),
        () =>
          `round ${i} expected SCREEN_${tab} SESSION_${selected}; ` +
          `viewport baseY=${terminal.buffer.active.baseY} length=${terminal.buffer.active.length} ` +
          `cols=${terminal.cols}\n${screen()}`,
      );
      await delay(60);
      const pulse = fetch(base + "/test/pulse/" + i, { method: "POST" });
      if (tab === "chat") {
        const text = `draft${i} `;
        typed[selected] += text;
        stdin.press(text);
      } else stdin.press("\x1b[200~pfx1\x1b[201~");
      assert.equal((await pulse).status, 200);
      if (selected === 0)
        await until(() =>
          useAppStore
            .getState()
            .messages.some((message) =>
              message.content.includes(`TRANSCRIPT_PULSE_${i}`),
            ),
        );
      if (i % 5 === 4) {
        const before = recovered;
        assert.equal(
          (await fetch(base + "/test/drop", { method: "POST" })).status,
          200,
        );
        await until(() => recovered > before);
        await delay(80);
        if (selected === 0)
          await until(() => !!useAppStore.getState().approval);
      }
      await delay(40);
      if (tab === "chat")
        assert.ok(
          screen().includes(typed[selected].trim()),
          "draft survived output and session switches",
        );
    }
    instance.rerender(view("approval", first.session_id));
    useAppStore.getState().actions.setSessionId(first.session_id);
    await until(
      () =>
        !!useAppStore.getState().approval && screen().includes("Approve Once"),
    );
    assert.deepEqual(
      listeners(),
      baseline,
      "panel/reconnect listeners return to baseline",
    );
    await fetch(base + "/test/lose/tool_approval_response", { method: "POST" });
    stdin.press("11"); // A text chunk is not two numeric approval shortcuts.
    stdin.press("1");
    stdin.press("1");
    await until(() => !useAppStore.getState().approval);
    await delay(150);
    const state = (await (await fetch(base + "/test/state")).json()) as {
      requests: Array<{ method: string }>;
    };
    assert.equal(
      state.requests.filter(
        (request) => request.method === "tool_approval_response",
      ).length,
      1,
    );
    assert.equal(
      state.requests.filter((request) =>
        ["git_stage", "git_discard", "update_thread", "fork_session"].includes(
          request.method,
        ),
      ).length,
      0,
    );
    await fetch(base + "/test/finish", { method: "POST" });
  } finally {
    instance.unmount();
    instance.cleanup();
    await delay(30);
    assert.deepEqual(
      listeners(),
      originalListeners,
      "all screen listeners released on unmount",
    );
    assert.equal(stdin.listenerCount("readable"), 0);
    terminal.dispose();
  }
}
