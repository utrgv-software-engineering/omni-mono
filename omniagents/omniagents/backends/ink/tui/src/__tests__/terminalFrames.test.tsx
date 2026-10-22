/**
 * What actually reaches the terminal, frame by frame.
 *
 * Under tmux the TUI flickered on every keystroke, spinner tick and
 * streamed event: each frame erased the screen and rewrote all of it, and
 * tmux repaints the whole pane at the end of every synchronized update.
 * These tests drive the real Ink renderer (with this repo's patch) into a
 * fake terminal using the TUI's own render options and assert on the
 * bytes: no erase between frames, only changed lines rewritten, DEC 2026
 * outside a multiplexer and none inside one, and a frame that follows
 * the terminal's height through a resize — and, replaying the bytes into
 * a terminal grid, that the screen always ends up showing exactly the
 * frame Ink composed.
 */
import { EventEmitter } from "node:events";
import React, { useEffect, useState } from "react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { inkRenderOptions } from "../renderOptions.js";
import { useTerminalSize } from "../hooks/useTerminalSize.js";
import { ScreenGrid } from "./helpers/screen.js";

type Ink = typeof import("ink");
let ink: Ink;

beforeAll(async () => {
  // Ink decides "am I in CI?" once, at import, and in CI it writes no
  // frames at all. The suite runs in CI, so import it with that unset.
  delete process.env.CI;
  delete process.env.CONTINUOUS_INTEGRATION;
  ink = await import("ink");
});

class FakeTerminal extends EventEmitter {
  isTTY = true;
  writes: string[] = [];
  screen: ScreenGrid;
  constructor(
    public columns: number,
    public rows: number,
  ) {
    super();
    this.screen = new ScreenGrid(columns, rows);
  }
  write(data: string) {
    this.writes.push(String(data));
    this.screen.feed(String(data));
    return true;
  }
  resize(columns: number, rows: number) {
    this.columns = columns;
    this.rows = rows;
    this.screen.resize(columns, rows);
    this.emit("resize");
  }
  get output() {
    return this.writes.join("");
  }
}

class FakeStdin extends EventEmitter {
  isTTY = false;
  setEncoding() {}
  setRawMode() {}
  read() {
    return null;
  }
  ref() {}
  unref() {}
}

const SYNC_ON = "\x1b[?2026h";
const SYNC_OFF = "\x1b[?2026l";
const ERASE_SCREEN = /\x1b\[2J|\x1b\[H\x1b\[J|\x1b\[J/;
const plain = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "").trimEnd();

/** What the screen must show for a composed frame: its first `rows` lines. */
function expectedScreen(frame: string, rows: number): string[] {
  const lines = frame.split("\n").slice(0, rows).map(plain);
  while (lines.length < rows) lines.push("");
  return lines;
}

const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));

/** Wait for a condition rather than a fixed time: the suite runs loaded. */
async function until(check: () => boolean, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (!check() && Date.now() < deadline) await tick(10);
}

const lastComposed = () => composed.at(-1) ?? "";

let unmount: (() => void) | null = null;
afterEach(() => {
  unmount?.();
  unmount = null;
});

/** A full-height screen whose only moving part is one status line. */
function Screen({ ticks }: { ticks: number }) {
  const { Box, Text } = ink;
  const { rows } = useTerminalSize();
  const [n, setN] = useState(0);
  useEffect(() => {
    if (n >= ticks) return;
    const id = setTimeout(() => setN((v) => v + 1), 50);
    return () => clearTimeout(id);
  }, [n, ticks]);
  return (
    <Box flexDirection="column" height={rows - 1}>
      <Box flexShrink={0}>
        <Text>header</Text>
      </Box>
      <Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden">
        {Array.from({ length: 40 }, (_, i) => (
          <Box key={i} flexShrink={0}>
            <Text>{`transcript line ${i}`}</Text>
          </Box>
        ))}
      </Box>
      <Box flexShrink={0}>
        <Text>{`spinner ${n}`}</Text>
      </Box>
      <Box flexShrink={0}>
        <Text>composer</Text>
      </Box>
    </Box>
  );
}

let resizeMounted: (columns: number, rows: number) => void = () => {};

/** Every frame Ink composed, before the writer clips it to the screen. */
let composed: string[] = [];

function mount(node: React.ReactElement, env: NodeJS.ProcessEnv) {
  const stdout = new FakeTerminal(80, 20);
  composed = [];
  const instance = ink.render(node, {
    ...inkRenderOptions({ debug: false, env, consoleOffTerminal: true }),
    onRender: ({ output }) => {
      if (output !== undefined) composed.push(output);
    },
    stdout: stdout as unknown as NodeJS.WriteStream,
    stderr: new FakeTerminal(80, 20) as unknown as NodeJS.WriteStream,
    stdin: new FakeStdin() as unknown as NodeJS.ReadStream,
  });
  unmount = () => instance.unmount();
  resizeMounted = (columns, rows) => stdout.resize(columns, rows);
  return stdout;
}

describe("frames on the wire", () => {
  it("rewrites only the changed line, never erasing the screen between frames", async () => {
    const term = mount(<Screen ticks={8} />, {});
    await until(() => lastComposed().includes("spinner 0"));
    const first = term.writes.length;
    await until(() => lastComposed().includes("spinner 8"));
    await tick();
    const later = term.writes.slice(first).join("");
    const frames = later.split(SYNC_ON).slice(1);

    // Eight ticks; the 30 fps throttle may merge a few under load.
    expect(frames.length).toBeGreaterThanOrEqual(3);
    expect(later).not.toMatch(ERASE_SCREEN);
    for (const frame of frames) {
      expect(frame).toContain(SYNC_OFF);
      // One 80-column line plus cursor movement, not a 20-row screen.
      expect(frame.length).toBeLessThan(200);
      expect(frame).toMatch(/spinner \d/);
      expect(frame).not.toContain("transcript line");
    }
  });

  it("leaves synchronized output off inside tmux, where it forces full-pane repaints", async () => {
    const term = mount(<Screen ticks={4} />, {
      TMUX: "/tmp/tmux-1/default,1,0",
    });
    await until(() => lastComposed().includes("spinner 4"));
    await tick();
    expect(term.output).not.toContain(SYNC_ON);
    expect(term.output).not.toContain(SYNC_OFF);
    const spinnerWrites = term.writes.filter((w) => /spinner [1-9]/.test(w));
    expect(spinnerWrites.length).toBeGreaterThanOrEqual(2);
    for (const w of spinnerWrites) {
      expect(w).not.toMatch(ERASE_SCREEN);
      expect(w).not.toContain("transcript line");
    }
  });

  it("re-lays the screen out to the new height on a resize", async () => {
    mount(<Screen ticks={0} />, {});
    await until(() => lastComposed().split("\n").length === 19);
    expect(lastComposed().split("\n")).toHaveLength(19);
    resizeMounted(80, 12);
    await until(() => lastComposed().split("\n").length <= 12);
    // Without a re-render the tree keeps its 20-row height, the frame is
    // taller than the terminal and the alternate screen cuts the bottom
    // (status line, composer) off.
    const last = lastComposed().split("\n");
    expect(last.length).toBeLessThanOrEqual(12);
    expect(last.at(-1)).toContain("composer");
  });

  it.each([
    ["a plain terminal", {}],
    ["tmux", { TMUX: "/tmp/tmux-1/default,1,0" }],
  ])(
    "leaves the screen showing exactly the composed frame (%s)",
    async (_label, env) => {
      const term = mount(<Screen ticks={4} />, env);
      const settled = (rows: number) => () =>
        lastComposed().includes("spinner 4") &&
        lastComposed().split("\n").length === rows - 1 &&
        term.screen.lines().join("\n") ===
          expectedScreen(lastComposed(), rows).join("\n");
      await until(settled(20));
      expect(term.screen.lines()).toEqual(expectedScreen(lastComposed(), 20));
      // Shrinking changes the last row and drops one: the rows the shorter
      // frame no longer covers must be cleared, the new last row kept.
      for (const rows of [12, 16, 9]) {
        term.resize(80, rows);
        await until(settled(rows));
        expect(term.screen.lines()).toEqual(
          expectedScreen(lastComposed(), rows),
        );
      }
    },
  );
});
