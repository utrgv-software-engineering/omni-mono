import React from "react";
import { mkdtemp, readFile, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import stripAnsi from "strip-ansi";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { KeypressProvider } from "../../contexts/KeypressContext.js";
import type { ArtifactEntry } from "../artifact-model.js";
import { cleanup, render } from "ink-testing-library";

const plain = (frame?: string) => stripAnsi(frame ?? "");

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

let ArtifactsPanel: (typeof import("../ArtifactsPanel.js"))["ArtifactsPanel"];
let useAppStore: (typeof import("../../store/appStore.js"))["useAppStore"];

beforeAll(async () => {
  ({ ArtifactsPanel } = await import("../ArtifactsPanel.js"));
  ({ useAppStore } = await import("../../store/appStore.js"));
});

afterEach(() => {
  cleanup();
  useAppStore.getState().actions.clearArtifacts();
});

function mount(artifacts: ArtifactEntry[], saveDir?: string) {
  useAppStore.getState().actions.clearArtifacts();
  for (const item of artifacts) {
    useAppStore.getState().actions.addArtifact(item);
  }
  const onUnfocus = vi.fn();
  const instance = render(
    <KeypressProvider>
      <ArtifactsPanel focused onUnfocus={onUnfocus} saveDir={saveDir} />
    </KeypressProvider>,
  );
  return { ...instance, onUnfocus };
}

describe("ArtifactsPanel list", () => {
  it("teaches what makes artifacts appear when there are none", async () => {
    const { lastFrame } = mount([]);
    await settle();
    expect(plain(lastFrame())).toContain("display_artifact");
  });

  it("lists titles with their mode and size", async () => {
    const { lastFrame } = mount([entry()]);
    await settle();
    const frame = plain(lastFrame());
    expect(frame).toContain("Weekly Report");
    expect(frame).toContain("markdown");
  });

  it("replaces rather than appends when an id is re-emitted", async () => {
    const { lastFrame } = mount([entry(), entry({ content: "revised" })]);
    await settle();
    const frame = plain(lastFrame());
    expect(frame).toContain("Artifacts");
    // One row, and it announces that it changed.
    expect(frame.match(/Weekly Report/g)).toHaveLength(1);
    expect(frame).toContain("rev 2");
  });

  it("closes on escape", async () => {
    const { stdin, onUnfocus } = mount([entry()]);
    await settle();
    stdin.write(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(onUnfocus).toHaveBeenCalled();
  });
});

describe("ArtifactsPanel preview", () => {
  it("shows text content on enter", async () => {
    const { stdin, lastFrame } = mount([entry()]);
    await settle();
    stdin.write(KEY.enter);
    await settle();
    expect(plain(lastFrame())).toContain("line one");
  });

  it("REFUSES to preview a binary artifact", async () => {
    // The defect this exists for: previewing a pdf/image would print its
    // base64 payload into the TTY. Enter must be inert and say why.
    const { stdin, lastFrame } = mount([
      entry({ title: "Scan", mode: "pdf", content: PDF_DATA_URL }),
    ]);
    await settle();
    stdin.write(KEY.enter);
    await settle();
    const frame = plain(lastFrame());
    expect(frame).toContain("press w to save");
    expect(frame).not.toContain("aGVsbG8=");
  });

  it("returns to the list on escape without closing the drawer", async () => {
    const { stdin, lastFrame, onUnfocus } = mount([entry()]);
    await settle();
    stdin.write(KEY.enter);
    await settle();
    stdin.write(KEY.escape);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await settle();
    expect(onUnfocus).not.toHaveBeenCalled();
    expect(plain(lastFrame())).toContain("enter preview");
  });
});

describe("ArtifactsPanel save", () => {
  it("writes text to the save directory", async () => {
    const dir = await mkdtemp(path.join(tmpdir(), "ink-artifacts-"));
    const { stdin, lastFrame } = mount([entry()], dir);
    await settle();
    stdin.write("w");
    await settle(60);
    expect(plain(lastFrame())).toContain("saved");
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
    stdin.write("w");
    await settle(60);
    const written = await readFile(path.join(dir, "Scan.pdf"), "utf8");
    expect(written).toBe("hello");
  });

  it("keeps an agent-authored title from escaping the save directory", async () => {
    // The title is untrusted. `../../evil` must land inside `dir`.
    const dir = await mkdtemp(path.join(tmpdir(), "ink-artifacts-"));
    const { stdin } = mount([entry({ title: "../../evil" })], dir);
    await settle();
    stdin.write("w");
    await settle(60);
    expect(await readdir(dir)).toEqual(["evil.md"]);
  });

  it("reports a failed write instead of appearing to succeed", async () => {
    const { stdin, lastFrame } = mount(
      [entry()],
      path.join(tmpdir(), "ink-artifacts-does-not-exist"),
    );
    await settle();
    stdin.write("w");
    await settle(60);
    expect(plain(lastFrame())).toContain("save failed");
  });
});
