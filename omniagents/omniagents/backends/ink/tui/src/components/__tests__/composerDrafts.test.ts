import { expect, it } from "vitest";
import { ComposerDrafts } from "../SessionComposer.js";

it("releases empty retired drafts repeatedly without evicting unsent text", () => {
  const drafts = new ComposerDrafts();
  const unsent = drafts.get("saved");
  unsent.current = {
    lines: ["keep me"],
    cursorRow: 0,
    cursorCol: 3,
    scrollOffset: 0,
    historyIndex: 0,
    draft: "",
  };
  for (let i = 0; i < 100; i++) {
    const empty = drafts.get(`session-${i}`);
    empty.release!();
    const replacement = drafts.get(`session-${i}`);
    expect(replacement).not.toBe(empty);
    replacement.release!();
    unsent.release!();
    expect(drafts.get("saved")).toBe(unsent);
  }
});

it("retains mounted and in-flight drafts until their owners release them", () => {
  const drafts = new ComposerDrafts();
  const draft = drafts.get("a", "question");
  draft.notify = () => {};
  draft.release!();
  expect(drafts.get("a", "question")).toBe(draft);
  draft.notify = undefined;
  draft.pending = true;
  draft.release!();
  expect(drafts.get("a", "question")).toBe(draft);
  draft.pending = false;
  draft.release!();
  expect(drafts.get("a", "question")).not.toBe(draft);
});
