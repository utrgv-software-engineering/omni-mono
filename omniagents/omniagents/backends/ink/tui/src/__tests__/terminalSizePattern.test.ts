/**
 * Guards the one-way-to-read-the-terminal-size rule.
 *
 * A render-time `stdout.rows` / `stdout.columns` read goes stale on a
 * resize: Ink repaints the existing tree immediately, so the layout keeps
 * the old height, the frame overflows the shrunk screen and the alternate
 * buffer clips the composer off until something else re-renders.
 * `hooks/useTerminalSize` subscribes to resizes; this scan stops the next
 * component from going back to reading the stream directly.
 */
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const OWNER = path.join("hooks", "useTerminalSize.ts");

function sourceFiles(dir = SRC, prefix = ""): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = path.join(prefix, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "fixtures") continue;
      out.push(...sourceFiles(path.join(dir, entry.name), rel));
    } else if (
      /\.tsx?$/.test(entry.name) &&
      !/\.test\.tsx?$/.test(entry.name)
    ) {
      out.push(rel);
    }
  }
  return out;
}

const SIZE_READ = /stdout\??\.(rows|columns)\b/;

describe("terminal size", () => {
  it("is read only through useTerminalSize", () => {
    const offenders = sourceFiles()
      .filter((rel) => rel !== OWNER)
      .filter((rel) =>
        SIZE_READ.test(readFileSync(path.join(SRC, rel), "utf8")),
      );
    expect(offenders).toEqual([]);
  });
});
