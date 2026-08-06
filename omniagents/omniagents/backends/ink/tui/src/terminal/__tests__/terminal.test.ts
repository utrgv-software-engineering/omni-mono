import { describe, expect, it } from "vitest";
import { XtermTerminalState } from "../state.js";
import { keyToBytes } from "../keys.js";

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

describe("XtermTerminalState", () => {
  it("feeds bytes and reads plain lines back", async () => {
    const state = new XtermTerminalState(40, 5);
    state.feed("hello\r\nworld");
    await settle();
    const lines = state.readLines(0, 2);
    expect(lines[0]).toBe("hello");
    expect(lines[1]).toBe("world");
    expect(state.altScreenActive()).toBe(false);
    state.dispose();
  });

  it("reconstructs SGR runs for styled cells", async () => {
    const state = new XtermTerminalState(40, 5);
    state.feed("a \x1b[31mred\x1b[0m b");
    await settle();
    const line = state.readLines(0, 1)[0]!;
    expect(line).toContain("\x1b[0;31mred");
    expect(line.endsWith("\x1b[0m")).toBe(true);
    // Plain text on both sides survives.
    expect(line.replace(/\x1b\[[0-9;]*m/g, "")).toBe("a red b");
    state.dispose();
  });

  it("tracks scrollback and the viewport base", async () => {
    const state = new XtermTerminalState(20, 3, 100);
    for (let i = 0; i < 10; i += 1) state.feed(`line${i}\r\n`);
    await settle();
    expect(state.lineCount()).toBeGreaterThan(3);
    expect(state.baseY()).toBe(state.lineCount() - 3);
    expect(state.readLines(0, 1)[0]).toBe("line0");
    state.dispose();
  });

  it("detects the alternate screen (full-screen apps)", async () => {
    const state = new XtermTerminalState(20, 3);
    state.feed("\x1b[?1049h");
    await settle();
    expect(state.altScreenActive()).toBe(true);
    state.dispose();
  });

  it("coalesces update notifications", async () => {
    const state = new XtermTerminalState(20, 3);
    let updates = 0;
    state.onUpdate(() => {
      updates += 1;
    });
    for (let i = 0; i < 50; i += 1) state.feed("x");
    await settle();
    expect(updates).toBeGreaterThan(0);
    expect(updates).toBeLessThan(10);
    state.dispose();
  });
});

describe("keyToBytes", () => {
  it("translates the line-oriented vocabulary", () => {
    expect(keyToBytes("", { return: true })).toBe("\r");
    expect(keyToBytes("", { backspace: true })).toBe("\x7f");
    expect(keyToBytes("", { upArrow: true })).toBe("\x1b[A");
    expect(keyToBytes("c", { ctrl: true })).toBe("\x03");
    expect(keyToBytes("ls -la", {})).toBe("ls -la");
    expect(keyToBytes("", { escape: true, meta: true })).toBe("\x1b");
    expect(keyToBytes("f", { meta: true })).toBe("\x1bf");
  });

  it("drops mouse and focus reports instead of typing them", () => {
    expect(keyToBytes("[<0;70;35M", { meta: true })).toBeNull();
    expect(keyToBytes("[<0;70;35m", { meta: true })).toBeNull();
    expect(keyToBytes("[<0;70;35M", {})).toBeNull();
    expect(keyToBytes("[I", { meta: true })).toBeNull();
    expect(keyToBytes("[O", { meta: true })).toBeNull();
    // Real alt+key chords still forward.
    expect(keyToBytes("f", { meta: true })).toBe("\x1bf");
  });

  it("reserves the detach chord and scrollback keys", () => {
    expect(keyToBytes("]", { ctrl: true })).toBeNull();
    expect(keyToBytes("", { pageUp: true })).toBeNull();
    expect(keyToBytes("", { pageDown: true })).toBeNull();
  });
});
