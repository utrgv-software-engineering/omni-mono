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
import { cleanup, render } from "ink-testing-library";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

const settle = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));

const WHEEL_UP = "\u001b[<64;5;5M";
const ARROW_DOWN = "\u001b[B";

let useInput: (typeof import("ink"))["useInput"];
let MouseProvider: (typeof import("../MouseContext.js"))["MouseProvider"];
let useMouseContext: (typeof import("../MouseContext.js"))["useMouseContext"];

beforeAll(async () => {
  const ink = await import("ink");
  useInput = ink.useInput;
  ({ MouseProvider, useMouseContext } = await import("../MouseContext.js"));
});

afterEach(cleanup);

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
  const { stdin } = render(
    <MouseProvider mouseEventsEnabled>
      <Probe />
    </MouseProvider>,
  );
  await settle(80);
  return { stdin, seen };
}

describe("mouse report buffer", () => {
  it("parses a wheel report", async () => {
    const { stdin, seen } = await mount();
    stdin.write(WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });

  it("still parses wheel reports after an arrow key", async () => {
    const { stdin, seen } = await mount();
    stdin.write(ARROW_DOWN);
    await settle();
    stdin.write(WHEEL_UP);
    stdin.write(WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up", "scroll-up"]);
  });

  it("parses a wheel report arriving in the same chunk as a key", async () => {
    const { stdin, seen } = await mount();
    stdin.write(ARROW_DOWN + WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });

  it("holds a split report until its terminator arrives", async () => {
    // A prefix of an SGR report must NOT be skipped as foreign — the rest
    // of it is still coming.
    const { stdin, seen } = await mount();
    stdin.write("\u001b[<64;5");
    await settle();
    expect(seen).toEqual([]);
    stdin.write(";5M");
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });

  it("survives a bare escape followed by a report", async () => {
    const { stdin, seen } = await mount();
    stdin.write("\u001b");
    await settle();
    stdin.write(WHEEL_UP);
    await settle();
    expect(seen).toEqual(["scroll-up"]);
  });
});
