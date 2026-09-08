import React from "react";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { mkdtemp, readFile, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { ArtifactEntry } from "../artifact-model.js";

class TestStdout extends PassThrough {
  frames: string[] = [];
  isTTY = true;
  columns = 100;
  rows = 40;

  constructor() {
    super();
    this.setEncoding("utf8");
    this.on("data", (chunk) => {
      if (typeof chunk === "string") this.frames.push(chunk);
    });
  }

  lastFrame(): string {
    for (let index = this.frames.length - 1; index >= 0; index -= 1) {
      const frame = stripAnsi(this.frames[index] ?? "");
      if (frame.trim()) return frame;
    }
    return "";
  }
}

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
  press(sequence: string) {
    this.queue.push(sequence);
    this.emit("readable");
  }
}

const KEY = { down: "[B", enter: "\r", escape: "" } as const;
const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

/** `data:application/pdf;base64,` + "hello" */
const PDF_DATA_URL = "data:application/pdf;base64,aGVsbG8=";

function entry(overrides: Partial<ArtifactEntry> = {}): ArtifactEntry {
  return {
    artifactId: "a-1",
    title: "Weekly Report",
    content: "line one\nline two",
    mode: "markdown",
    sessionId: "s-1",
    updatedAt: 1_700_000_000_000,
    revision: 0,
    ...overrides,
  };
}

type Render = (typeof import("ink"))["render"];
const instances: Array<ReturnType<Render>> = [];
let inkRender: Render;
let ArtifactsPanel: (typeof import("../ArtifactsPanel.js"))["ArtifactsPanel"];
let useAppStore: (typeof import("../../store/appStore.js"))["useAppStore"];

beforeAll(async () => {
  inkRender = (await import("ink")).render;
  ({ ArtifactsPanel } = await import("../ArtifactsPanel.js"));
  ({ useAppStore } = await import("../../store/appStore.js"));
});

afterEach(() => {
  while (instances.length) {
    const instance = instances.pop();
    instance?.unmount();
    instance?.cleanup();
  }
  useAppStore.getState().actions.clearArtifacts();
});

function mount(artifacts: ArtifactEntry[], saveDir?: string) {
  useAppStore.getState().actions.clearArtifacts();
  for (const item of artifacts) {
    useAppStore.getState().actions.addArtifact(item);
  }
  const stdout = new TestStdout();
  const stdin = new FakeStdin();
  const onUnfocus = vi.fn();
  const instance = inkRender(
    <KeypressProvider>
      <ArtifactsPanel focused onUnfocus={onUnfocus} saveDir={saveDir} />
    </KeypressProvider>,
    {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  instances.push(instance);
  return { stdout, stdin, onUnfocus };
}

describe("ArtifactsPanel list", () => {
  it("teaches what makes artifacts appear when there are none", async () => {
    const { stdout } = mount([]);
    await settle();
    expect(stdout.lastFrame()).toContain("display_artifact");
  });

  it("lists titles with their mode and size", async () => {
    const { stdout } = mount([entry()]);
    await settle();
    const frame = stdout.lastFrame();
    expect(frame).toContain("Weekly Report");
    expect(frame).toContain("markdown");
  });

  it("replaces rather than appends when an id is re-emitted", async () => {
    const { stdout } = mount([entry(), entry({ content: "revised" })]);
    await settle();
    const frame = stdout.lastFrame();
    expect(frame).toContain("Artifacts");
    // One row, and it announces that it changed.
    expect(frame.match(/Weekly Report/g)).toHaveLength(1);
    expect(frame).toContain("rev 2");
  });

  it("closes on escape", async () => {
    const { stdin, onUnfocus } = mount([entry()]);
    await settle();
    stdin.press(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(onUnfocus).toHaveBeenCalled();
  });
});

describe("ArtifactsPanel preview", () => {
  it("shows text content on enter", async () => {
    const { stdin, stdout } = mount([entry()]);
    await settle();
    stdin.press(KEY.enter);
    await settle();
    expect(stdout.lastFrame()).toContain("line one");
  });

  it("REFUSES to preview a binary artifact", async () => {
    // The defect this exists for: previewing a pdf/image would print its
    // base64 payload into the TTY. Enter must be inert and say why.
    const { stdin, stdout } = mount([
      entry({ title: "Scan", mode: "pdf", content: PDF_DATA_URL }),
    ]);
    await settle();
    stdin.press(KEY.enter);
    await settle();
    const frame = stdout.lastFrame();
    expect(frame).toContain("press w to save");
    expect(frame).not.toContain("aGVsbG8=");
  });

  it("returns to the list on escape without closing the drawer", async () => {
    const { stdin, stdout, onUnfocus } = mount([entry()]);
    await settle();
    stdin.press(KEY.enter);
    await settle();
    stdin.press(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(onUnfocus).not.toHaveBeenCalled();
    expect(stdout.lastFrame()).toContain("enter preview");
  });
});

describe("ArtifactsPanel save", () => {
  it("writes text to the save directory", async () => {
    const dir = await mkdtemp(path.join(tmpdir(), "ink-artifacts-"));
    const { stdin, stdout } = mount([entry()], dir);
    await settle();
    stdin.press("w");
    await settle(60);
    expect(stdout.lastFrame()).toContain("saved");
    const written = await readFile(path.join(dir, "Weekly-Report.md"), "utf8");
    expect(written).toBe("line one\nline two");
  });

  it("decodes a binary payload rather than writing its base64", async () => {
    const dir = await mkdtemp(path.join(tmpdir(), "ink-artifacts-"));
    const { stdin } = mount(
      [entry({ title: "Scan", mode: "pdf", content: PDF_DATA_URL })],
      dir,
    );
    await settle();
    stdin.press("w");
    await settle(60);
    const written = await readFile(path.join(dir, "Scan.pdf"), "utf8");
    expect(written).toBe("hello");
  });

  it("keeps an agent-authored title from escaping the save directory", async () => {
    // The title is untrusted. `../../evil` must land inside `dir`.
    const dir = await mkdtemp(path.join(tmpdir(), "ink-artifacts-"));
    const { stdin } = mount([entry({ title: "../../evil" })], dir);
    await settle();
    stdin.press("w");
    await settle(60);
    expect(await readdir(dir)).toEqual(["evil.md"]);
  });

  it("reports a failed write instead of appearing to succeed", async () => {
    const { stdin, stdout } = mount(
      [entry()],
      path.join(tmpdir(), "ink-artifacts-does-not-exist"),
    );
    await settle();
    stdin.press("w");
    await settle(60);
    expect(stdout.lastFrame()).toContain("save failed");
  });
});
