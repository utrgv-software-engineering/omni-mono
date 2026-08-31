/**
 * Where ctrl+o leaves you.
 *
 * The transcript's two views are different projections of the same
 * conversation, with very different row counts, so the scroll offset the
 * old view was holding points somewhere unrelated in the new one.
 * Measured before this: parked at the bottom of an 8-turn transcript,
 * ctrl+o dropped you at turn 3.
 *
 * There is nothing to preserve — position cannot survive the change,
 * because VirtualizedList only measures rows it renders and extrapolates
 * the rest, so the coordinate space itself moves. Landing at the bottom
 * is the one position that means the same thing in both views.
 */

import React from "react";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

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
  push(sequence: string) {
    this.queue.push(sequence);
    this.emit("readable");
  }
}

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 60;
  rows = 30;
  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") this.frames.push(chunk);
    });
  }
  lastFrame(): string {
    for (let i = this.frames.length - 1; i >= 0; i -= 1) {
      const frame = stripAnsi(this.frames[i] ?? "");
      if (frame.trim()) return frame;
    }
    return "";
  }
}

const settle = (ms = 300) => new Promise((resolve) => setTimeout(resolve, ms));
const WHEEL_UP = "\u001b[<64;3;3M";

type Ink = typeof import("ink");
let ink: Ink;
let Scrollable: (typeof import("../Scrollable.js"))["Scrollable"];
let ScrollProvider: (typeof import("../../contexts/ScrollProvider.js"))["ScrollProvider"];
let MouseProvider: (typeof import("../../contexts/MouseContext.js"))["MouseProvider"];
let MessageListOptimized: (typeof import("../MessageListOptimized.js"))["MessageListOptimized"];
let store: typeof import("../../store/appStore.js");
let instance: { unmount: () => void; cleanup: () => void } | null = null;

beforeAll(async () => {
  ink = await import("ink");
  ({ Scrollable } = await import("../Scrollable.js"));
  ({ ScrollProvider } = await import("../../contexts/ScrollProvider.js"));
  ({ MouseProvider } = await import("../../contexts/MouseContext.js"));
  ({ MessageListOptimized } = await import("../MessageListOptimized.js"));
  store = await import("../../store/appStore.js");
});

afterEach(() => {
  instance?.unmount();
  instance?.cleanup();
  instance = null;
});

/** Eight turns, each a user line, four tool calls and a reply. */
function transcript() {
  const msgs: unknown[] = [];
  for (let t = 0; t < 8; t += 1) {
    msgs.push({ role: "user", content: `USER-${t}` });
    for (let i = 0; i < 4; i += 1) {
      msgs.push({
        role: "tool_call",
        content: "",
        toolCall: {
          tool: "read_file",
          input: `{"path":"f${t}_${i}.ts"}`,
          call_id: `c${t}_${i}`,
          running: false,
          output: "ok",
        },
      });
    }
    msgs.push({ role: "assistant", content: `REPLY-${t}` });
  }
  return msgs;
}

async function mount() {
  store.useAppStore.setState({
    messages: transcript() as never,
    transcriptDetail: false,
  });
  const stdin = new FakeStdin();
  const stdout = new TestStdout();
  const { render, Box } = ink;
  const Host = () => {
    const detail = store.useTranscriptDetail();
    const Input = () => {
      ink.useInput(() => {});
      return null;
    };
    return (
      <>
        <Input />
        <Box height={10}>
          <Scrollable
            hasFocus
            scrollToBottom
            maxHeight={10}
            flexGrow={1}
            pinToBottomToken={detail ? 1 : 0}
          >
            <MessageListOptimized />
          </Scrollable>
        </Box>
      </>
    );
  };
  instance = render(
    <MouseProvider mouseEventsEnabled>
      <ScrollProvider>
        <Host />
      </ScrollProvider>
    </MouseProvider>,
    {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  await settle();
  /** The last conversation marker on screen. */
  const tail = () => {
    const marks = stdout
      .lastFrame()
      .split("\n")
      .join(" ")
      .match(/(USER|REPLY)-\d/g);
    return marks?.[marks.length - 1] ?? "(none)";
  };
  const toggle = async () => {
    store.useAppStore.getState().actions.toggleTranscriptDetail();
    await settle(400);
  };
  return { stdin, tail, toggle };
}

describe("ctrl+o and the scroll position", () => {
  it("lands at the bottom from the bottom", async () => {
    // The regression: this put you at turn 3 of 8.
    const { tail, toggle } = await mount();
    expect(tail()).toBe("REPLY-7");
    await toggle();
    expect(tail()).toBe("USER-7");
  });

  it("lands at the bottom coming back", async () => {
    const { tail, toggle } = await mount();
    await toggle();
    await toggle();
    expect(tail()).toBe("REPLY-7");
  });

  it("lands at the bottom even when scrolled up", async () => {
    const { stdin, tail, toggle } = await mount();
    for (let i = 0; i < 4; i += 1) {
      stdin.push(WHEEL_UP);
      await settle(60);
    }
    expect(tail()).not.toBe("REPLY-7");
    await toggle();
    expect(tail()).toBe("REPLY-7");
  });

  it("does not drag the reader back while they scroll", async () => {
    // The pin follows the bottom, but only while it is armed. Scrolling
    // away disarms it — otherwise reading history would be impossible.
    const { stdin, tail } = await mount();
    for (let i = 0; i < 4; i += 1) {
      stdin.push(WHEEL_UP);
      await settle(60);
    }
    const parked = tail();
    await settle(300);
    expect(tail()).toBe(parked);
  });
});
