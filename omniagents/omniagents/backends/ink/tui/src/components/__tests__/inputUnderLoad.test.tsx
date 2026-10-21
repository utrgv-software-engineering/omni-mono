import React from "react";
import { performance } from "node:perf_hooks";
import { Box } from "ink";
import { render } from "ink-testing-library";
import stripAnsi from "strip-ansi";
import { expect, it, vi } from "vitest";
import { KeypressProvider } from "../../contexts/KeypressContext.js";
import { MouseProvider } from "../../contexts/MouseContext.js";
import { ScrollProvider } from "../../contexts/ScrollProvider.js";
import {
  useAppStore,
  useMessages,
  type Message,
} from "../../store/appStore.js";
import { Scrollable } from "../Scrollable.js";
import { MessageListOptimized } from "../MessageListOptimized.js";
import { MultiLineInputOptimized } from "../MultiLineInputOptimized.js";
import { SessionComposer, ComposerDrafts } from "../SessionComposer.js";

const plain = (frame?: string) => stripAnsi(frame ?? "");

it("keeps transcript content, drafts, and listeners consistent through repeated session and viewport changes", async () => {
  const saved = useAppStore.getState();
  const drafts = new ComposerDrafts();
  const onSubmit = vi.fn();
  const expected = ["", "", "", ""];
  const screen = () => plain(instance.lastFrame());
  function View({ session }: { session: number }) {
    const messages = useMessages();
    return (
      <Box flexDirection="column" height={40} width="100%">
        <Scrollable
          hasFocus
          scrollToBottom
          maxHeight={30}
          contentVersion={messages.length}
        >
          <MessageListOptimized />
        </Scrollable>
        <SessionComposer
          drafts={drafts}
          sessionId={String(session)}
          onSubmit={onSubmit}
          maxHeight={4}
        />
      </Box>
    );
  }
  const view = (session: number) => (
    <KeypressProvider>
      <MouseProvider>
        <ScrollProvider>
          <View session={session} />
        </ScrollProvider>
      </MouseProvider>
    </KeypressProvider>
  );
  useAppStore.getState().actions.setMessages([]);
  const instance = render(view(0));
  const { stdin, stdout } = instance;
  try {
    await vi.waitFor(() => expect(screen()).toContain(">"));
    const listeners = [
      stdin.listenerCount("readable"),
      stdout.listenerCount("resize"),
    ];
    for (let cycle = 0; cycle < 24; cycle++) {
      const session = cycle % 4;
      useAppStore.getState().actions.setMessages([
        { role: "assistant", content: `RESULT_${cycle}` },
        { role: "user", content: "tail" },
      ]);
      instance.rerender(view(session));
      if (cycle % 5 === 4) {
        const columns = cycle % 2 ? 80 : 100;
        // ink-testing-library's stdout is fixed at 100 columns; resize it
        // as a terminal does: new width, then a "resize" event.
        Object.defineProperty(stdout, "columns", {
          value: columns,
          configurable: true,
        });
        stdout.emit("resize");
      }
      await vi.waitFor(() => expect(screen()).toContain(`RESULT_${cycle}`));
      const addition = `_${cycle}`;
      expected[session] += addition;
      stdin.write(addition);
      await vi.waitFor(() =>
        expect(screen()).toContain("> " + expected[session]),
      );
      expect([
        stdin.listenerCount("readable"),
        stdout.listenerCount("resize"),
      ]).toEqual(listeners);
    }
    for (let session = 0; session < 4; session++) {
      instance.rerender(view(session));
      await vi.waitFor(() =>
        expect(screen()).toContain("> " + expected[session]),
      );
      stdin.write("\r");
      await vi.waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith(expected[session]),
      );
    }
    expect(onSubmit).toHaveBeenCalledTimes(4);
  } finally {
    instance.unmount();
    useAppStore.setState(saved, true);
  }
  expect(stdin.listenerCount("readable")).toBe(0);
  expect(stdout.listenerCount("resize")).toBe(0);
}, 20000);

it.each([100, 2000])(
  "preserves typing and paste with %i historical messages and streaming updates",
  async (count) => {
    const onSubmit = vi.fn();
    const saved = useAppStore.getState();
    const history: Message[] = Array.from({ length: count }, (_, index) => ({
      role: index % 2 ? "assistant" : "user",
      content: `History ${index}: **result**\n\nA paragraph with enough text to exercise markdown and wrapping.`,
    }));
    useAppStore.getState().actions.setMessages(history);
    function Transcript() {
      const messages = useMessages();
      return (
        <Scrollable
          hasFocus
          scrollToBottom
          maxHeight={30}
          contentVersion={messages.length}
        >
          <MessageListOptimized />
        </Scrollable>
      );
    }
    const started = performance.now();
    const instance = render(
      <KeypressProvider>
        <MouseProvider>
          <ScrollProvider>
            <Box flexDirection="column" height={40} width={100}>
              <Transcript />
              <MultiLineInputOptimized onSubmit={onSubmit} maxHeight={4} />
            </Box>
          </ScrollProvider>
        </MouseProvider>
      </KeypressProvider>,
    );
    const { stdin } = instance;
    const screen = () => plain(instance.lastFrame());
    let updates = 0;
    let timer: ReturnType<typeof setInterval> | undefined;
    try {
      await vi.waitFor(() => expect(screen()).toContain(">"), {
        timeout: 15000,
        interval: 5,
      });
      const startupMs = performance.now() - started;
      timer = setInterval(() => {
        updates++;
        useAppStore.getState().actions.setMessages([
          ...history,
          {
            role: "tool_call",
            content: "",
            toolCall: {
              tool: "read_file",
              input: '{"path":"example.ts"}',
              call_id: "live",
              running: true,
              output: `Read ${updates} chunks`,
            },
          },
          {
            role: "assistant",
            content:
              `Streaming **response** ${updates}: ` + "token ".repeat(updates),
          },
        ]);
      }, 5);
      const latency: number[] = [];
      let draft = "";
      for (let i = 0; i < 24; i++) {
        const keyStart = performance.now();
        const text = String.fromCharCode(97 + i);
        draft += text;
        stdin.write(text);
        await vi.waitFor(() => expect(screen()).toContain("> " + draft), {
          timeout: 2000,
          interval: 1,
        });
        latency.push(performance.now() - keyStart);
      }
      // Feed faster than the display frame rate without waiting for each paint.
      const rapid = "0123456789".repeat(12);
      for (const key of rapid) {
        stdin.write(key);
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
      draft += rapid;
      const paste = "line one\n" + "x".repeat(32000) + "PASTE_END";
      const pasteStart = performance.now();
      stdin.write("\x1b[200~" + paste.slice(0, 16000));
      await new Promise((resolve) => setTimeout(resolve, 10));
      stdin.write(paste.slice(16000) + "\x1b[201~");
      await vi.waitFor(() => expect(screen()).toContain("PASTE_END"), {
        timeout: 2000,
        interval: 1,
      });
      const pasteMs = performance.now() - pasteStart;
      // Submit directly after edits, without a React commit between them.
      stdin.write("\x1b[DZ\x7f\x1b[C!\r");
      await vi.waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith(draft + paste + "!"),
      );
      expect(onSubmit).toHaveBeenCalledTimes(1);
      expect(updates).toBeGreaterThan(0);
      latency.sort((a, b) => a - b);
      console.log(
        JSON.stringify({
          history: count,
          startupMs,
          updates,
          pasteMs,
          inputP50Ms: latency[12],
          inputP95Ms: latency[22],
          inputMaxMs: latency[23],
        }),
      );
    } finally {
      clearInterval(timer);
      instance.unmount();
      useAppStore.setState(saved, true);
    }
  },
  30000,
);
