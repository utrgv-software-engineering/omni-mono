/**
 * The mouse-report buffer must survive other escape sequences.
 *
 * Ink hands every stdin chunk to BOTH its own key parser and this
 * provider, so the provider sees arrow keys, function keys and anything
 * else the user presses — not just mouse reports. It has to skip them.
 *
 * It did not: a non-mouse escape sequence at the head of the buffer
 * matched no pattern, hit `if (idx === 0) break`, and stayed. Every
 * later chunk appended behind it, so no mouse report ever parsed again.
 * The wheel worked until the first arrow key and then never again —
 * which in practice reads as "the mouse wheel doesn't work".
 */

import React from "react";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
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
  /** Ink 7 reads stdin paused — "readable" + read(), never "data". */
  push(sequence: string) {
    this.queue.push(sequence);
    this.emit("readable");
  }
}

class NullStdout extends PassThrough {
  isTTY = true;
  columns = 40;
  rows = 10;
  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", () => {});
  }
}

const settle = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));

const WHEEL_UP = "\u001b[<64;5;5M";
const ARROW_DOWN = "\u001b[B";

type Render = (typeof import("ink"))["render"];
let inkRender: Render;
let useInput: (typeof import("ink"))["useInput"];
let MouseProvider: (typeof import("../MouseContext.js"))["MouseProvider"];
let useMouseContext: (typeof import("../MouseContext.js"))["useMouseContext"];
let instance: { unmount: () => void; cleanup: () => void } | null = null;

beforeAll(async () => {
  const ink = await import("ink");
  inkRender = ink.render;
  useInput = ink.useInput;
  ({ MouseProvider, useMouseContext } = await import("../MouseContext.js"));
});

afterEach(() => {
  instance?.unmount();
  instance?.cleanup();
  instance = null;
});

async function mount() {
  const seen: string[] = [];
  const Probe = () => {
    const { subscribe, unsubscribe } = useMouseContext();
    // Makes ink enable raw mode and attach its readable handler, which is
    // what feeds this provider — exactly as KeypressContext does.
    useInput(() => {});
    React.useEffect(() => {
      const handler = (event: { name: string }) => seen.push(event.name);
      subscribe(handler);
      return () => unsubscribe(handler);
    }, [subscribe, unsubscribe]);
    return null;
  };
  const stdin = new FakeStdin();
  instance = inkRender(
    <MouseProvider mouseEventsEnabled>
      <Probe />
    </MouseProvider>,
    {
      stdout: new NullStdout() as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  await settle(80);
  return { stdin, seen };
}

describe("mouse report buffer", () => {
  it("parses a wheel report", async () => {
    const { stdin, seen } = await mount();
    stdin.push(WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });

  it("still parses wheel reports after an arrow key", async () => {
    const { stdin, seen } = await mount();
    stdin.push(ARROW_DOWN);
    await settle();
    stdin.push(WHEEL_UP);
    stdin.push(WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up", "scroll-up"]);
  });

  it("parses a wheel report arriving in the same chunk as a key", async () => {
    const { stdin, seen } = await mount();
    stdin.push(ARROW_DOWN + WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });

  it("holds a split report until its terminator arrives", async () => {
    // A prefix of an SGR report must NOT be skipped as foreign — the rest
    // of it is still coming.
    const { stdin, seen } = await mount();
    stdin.push("\u001b[<64;5");
    await settle();
    expect(seen).toEqual([]);
    stdin.push(";5M");
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });

  it("survives a bare escape followed by a report", async () => {
    const { stdin, seen } = await mount();
    stdin.push("\u001b");
    await settle();
    stdin.push(WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });
});
