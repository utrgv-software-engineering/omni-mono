import { describe, expect, it } from "vitest";
import { slashResultMessage } from "../slash.js";

describe("slashResultMessage", () => {
  it("takes a string as the message", () => {
    expect(slashResultMessage("Context compacted.")).toBe("Context compacted.");
  });
  it("prefers message, then text, and never dumps JSON", () => {
    expect(slashResultMessage({ ok: true, message: "hi" })).toBe("hi");
    expect(slashResultMessage({ ok: true, text: "recap body" })).toBe(
      "recap body",
    );
    expect(slashResultMessage({ ok: true, goal: "ship" })).toBe("Done.");
    expect(slashResultMessage(null)).toBe("Done.");
    expect(slashResultMessage("  ")).toBe("Done.");
  });
});
