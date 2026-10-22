import { Writable } from "node:stream";
import { describe, expect, it } from "vitest";
import {
  inkRenderOptions,
  insideMultiplexer,
  routeConsoleOffTerminal,
} from "../renderOptions.js";

describe("insideMultiplexer", () => {
  it.each([
    [{ TMUX: "/tmp/tmux-1000/default,1,0", TERM: "xterm-256color" }, true],
    [{ STY: "1234.pts-0.host" }, true],
    // ssh from inside a pane: the variables stay behind, TERM travels.
    [{ TERM: "tmux-256color" }, true],
    [{ TERM: "screen-256color" }, true],
    [{ TERM: "xterm-kitty" }, false],
    [{}, false],
  ])("%o -> %s", (env, expected) => {
    expect(insideMultiplexer(env)).toBe(expected);
  });
});

describe("inkRenderOptions", () => {
  it("diffs frames into the alternate screen, synchronized on a plain terminal", () => {
    const options = inkRenderOptions({ debug: false, env: {} });
    expect(options.alternateBuffer).toBe(true);
    expect(options.incrementalRendering).toBe(true);
    expect(options.synchronizedOutput).toBe(true);
    expect(options.patchConsole).toBe(true);
  });

  it("drops synchronized output under tmux", () => {
    const options = inkRenderOptions({
      debug: false,
      env: { TMUX: "/tmp/tmux-1000/default,1,0" },
    });
    expect(options.incrementalRendering).toBe(true);
    expect(options.synchronizedOutput).toBe(false);
  });

  it("leaves the console alone once it is routed off the terminal", () => {
    const options = inkRenderOptions({
      debug: false,
      env: {},
      consoleOffTerminal: true,
    });
    expect(options.patchConsole).toBe(false);
  });
});

function sink(isTTY: boolean) {
  const chunks: string[] = [];
  const stream = new Writable({
    write(chunk, _enc, done) {
      chunks.push(String(chunk));
      done();
    },
  }) as unknown as NodeJS.WriteStream;
  (stream as { isTTY?: boolean }).isTTY = isTTY;
  return { stream, chunks };
}

describe("routeConsoleOffTerminal", () => {
  it("sends every console method to a non-terminal stderr", async () => {
    const { stream, chunks } = sink(false);
    const target = { ...console } as Console;
    expect(routeConsoleOffTerminal(stream, target)).toBe(true);
    target.log("to stdout");
    target.error("to stderr");
    await new Promise((r) => setImmediate(r));
    expect(chunks.join("")).toContain("to stdout");
    expect(chunks.join("")).toContain("to stderr");
  });

  it("leaves the console to Ink when stderr is the terminal", () => {
    const { stream } = sink(true);
    const target = { ...console } as Console;
    const log = target.log;
    expect(routeConsoleOffTerminal(stream, target)).toBe(false);
    expect(target.log).toBe(log);
  });
});
