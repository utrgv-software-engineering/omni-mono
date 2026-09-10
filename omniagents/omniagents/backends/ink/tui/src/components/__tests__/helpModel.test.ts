import { describe, expect, it } from "vitest";
import { helpSections } from "../help-model.js";

const allKeys = (screen: Parameters<typeof helpSections>[0]) =>
  helpSections(screen)
    .flatMap((section) => section.rows)
    .map((row) => row.keys);

describe("helpSections", () => {
  it("documents every global chord that used to live only in code", () => {
    const keys = allKeys("chat");
    for (const key of [
      "ctrl+o",
      "shift+↑ / shift+↓",
      "alt+1..5",
      "ctrl+b",
      "ctrl+n",
      "ctrl+g",
      "ctrl+k",
      "ctrl+p",
      "ctrl+c ctrl+c",
      "?",
    ]) {
      expect(keys).toContain(key);
    }
  });
  it("documents the popup keys and the composer newline", () => {
    const keys = allKeys("chat");
    expect(keys).toContain("/");
    expect(keys).toContain("@");
    expect(keys).toContain("shift+enter");
  });
  it("adds the current screen's keys and omits an empty section", () => {
    expect(helpSections("chat").map((s) => s.title)).not.toContain("Chat");
    expect(helpSections("term").map((s) => s.title)).toContain("Terminal");
    expect(allKeys("term")).toContain("ctrl+]");
  });
});
