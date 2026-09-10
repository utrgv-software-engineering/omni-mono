import { describe, expect, it } from "vitest";
import { drawerBudget, oneLine } from "../drawer-layout.js";

describe("drawerBudget", () => {
  it("takes at most half the terminal and never less than a few rows", () => {
    expect(drawerBudget(28)).toBe(10);
    expect(drawerBudget(50)).toBe(21);
    expect(drawerBudget(12)).toBe(4);
  });
});

describe("oneLine", () => {
  it("collapses newlines and runs of spaces", () => {
    expect(oneLine("  a\n  b\t\tc  ")).toBe("a b c");
  });
});
