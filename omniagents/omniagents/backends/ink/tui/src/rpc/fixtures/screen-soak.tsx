/** Real Python RPC + real Ink screens, invoked by test_ink_rpc.py through
 * python-server-client.ts, which hands over its helpers. */
import React, { useEffect, useState } from "react";
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { Box, Text } from "ink";
import { render } from "ink-testing-library";
import stripAnsi from "strip-ansi";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { MouseProvider } from "../../contexts/MouseContext.js";
import { ScrollProvider } from "../../contexts/ScrollProvider.js";
import { AgentsScreen } from "../../components/AgentsScreen.js";
import { SessionManager } from "../../components/SessionManager.js";
import { ApprovalDialog } from "../../components/ApprovalDialog.js";
import {
  SessionComposer,
  ComposerDrafts,
} from "../../components/SessionComposer.js";
import { MessageListOptimized } from "../../components/MessageListOptimized.js";
import { Scrollable } from "../../components/Scrollable.js";
import { useConversation } from "../../hooks/useConversation.js";
import { useAppStore, useApproval, useMessages } from "../../store/appStore.js";
import type { RPCClient } from "@omniagents/client/rpc/client";
import { uuidv4 } from "@omniagents/client/util/uuid";

type Kit = {
  until(check: () => boolean, describe?: () => string): Promise<void>;
  open(id: string): {
    session: { load(): Promise<unknown>; send(text: string): Promise<unknown> };
    close(): void;
  };
  listenerCounts(): Record<string, number>;
};

export async function runScreenSoak(
  client: RPCClient,
  base: string,
  { until, open, listenerCounts: listeners }: Kit,
) {
  const sessions = [uuidv4(), uuidv4()];
  const [first, second] = sessions.map(open) as [
    ReturnType<Kit["open"]>,
    ReturnType<Kit["open"]>,
  ];
  await first.session.load();
  await first.session.send("approval");
  await second.session.load();
  await second.session.send("completed");
  const drafts = new ComposerDrafts();
  const typed = ["", ""];
  const originalListeners = listeners();
  let recovered = 0;
  function Root({ tab, session }: { tab: string; session: string }) {
    const [ready, setReady] = useState(true);
    useEffect(
      () =>
        client.onConnectionState((state) => {
          if (state === "connected") recovered++;
          setReady(state === "connected");
        }),
      [],
    );
    const api = useConversation({
      client,
      sessionId: session,
      rpcReady: ready,
    });
    const approval = useApproval();
    const messages = useMessages();
    return (
      <Box flexDirection="column" height={38}>
        <Text>{`SCREEN_${tab} SESSION_${sessions.indexOf(session)}`}</Text>
        {tab === "agents" ? (
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
  useAppStore.getState().actions.setSessionId(sessions[0]!);
  const instance = render(view("approval", sessions[0]!));
  const { stdin, stdout } = instance;
  const screen = () => stripAnsi(instance.lastFrame() ?? "");
  try {
    await until(() => !!useAppStore.getState().approval);
    const baseline = listeners();
    const tabs = ["agents", "sessions", "chat", "approval"];
    for (let i = 0; i < 25; i++) {
      const tab = tabs[i % tabs.length];
      const selected = Math.floor(i / tabs.length) % 2;
      const session = sessions[selected]!;
      useAppStore.getState().actions.setSessionId(session);
      // ink-testing-library's stdout is fixed at 100 columns; resize it as a
      // terminal does: new width, then a "resize" event.
      Object.defineProperty(stdout, "columns", {
        value: i % 2 ? 80 : 100,
        configurable: true,
      });
      stdout.emit("resize");
      instance.rerender(view(tab, session));
      await until(
        () => screen().includes(`SCREEN_${tab} SESSION_${selected}`),
        () =>
          `round ${i} expected SCREEN_${tab} SESSION_${selected}; ` +
          `cols=${stdout.columns}\n${screen()}`,
      );
      await delay(60);
      const pulse = fetch(base + "/test/pulse/" + i, { method: "POST" });
      if (tab === "chat") {
        const text = `draft${i} `;
        typed[selected] += text;
        stdin.write(text);
      } else stdin.write("\x1b[200~pfx1\x1b[201~");
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
    instance.rerender(view("approval", sessions[0]!));
    useAppStore.getState().actions.setSessionId(sessions[0]!);
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
    stdin.write("11"); // A text chunk is not two numeric approval shortcuts.
    stdin.write("1");
    stdin.write("1");
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
    first.close();
    second.close();
    instance.unmount();
    instance.cleanup();
    await delay(30);
    assert.deepEqual(
      listeners(),
      originalListeners,
      "all screen listeners released on unmount",
    );
    assert.equal(stdin.listenerCount("readable"), 0);
  }
}
