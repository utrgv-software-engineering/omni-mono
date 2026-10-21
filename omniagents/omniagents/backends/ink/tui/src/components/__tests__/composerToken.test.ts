import { describe, expect, it } from "vitest";
import {
  detectComposerToken,
  nextDismissedToken,
  tokenKey,
} from "../composer-token.js";
import { inputReducer } from "../MultiLineInputOptimized.js";

describe("detectComposerToken", () => {
  it("sees a slash command only at the start of the message", () => {
    expect(detectComposerToken(["/mo"], 0, 3)).toEqual({
      kind: "slash",
      query: "mo",
      row: 0,
      start: 0,
      end: 3,
    });
    expect(detectComposerToken(["hello /mo"], 0, 9)).toBeNull();
    expect(detectComposerToken(["hello", "/mo"], 1, 3)).toBeNull();
  });

  it("stops offering the slash popup once the command has an argument", () => {
    expect(detectComposerToken(["/goal fix"], 0, 9)).toBeNull();
  });

  it("sees an @ mention at a word start on any row", () => {
    expect(detectComposerToken(["look at @src/ap"], 0, 15)).toEqual({
      kind: "mention",
      query: "src/ap",
      row: 0,
      start: 8,
      end: 15,
    });
    expect(detectComposerToken(["first", "@READ"], 1, 5)?.query).toBe("READ");
    expect(detectComposerToken(["email me@example"], 0, 16)).toBeNull();
  });

  it("only looks at text before the cursor", () => {
    expect(detectComposerToken(["@src later"], 0, 4)?.query).toBe("src");
    expect(detectComposerToken(["@src later"], 0, 10)).toBeNull();
  });

  it("keys a token by kind and position, not by query", () => {
    const a = detectComposerToken(["@s"], 0, 2)!;
    const b = detectComposerToken(["@src"], 0, 4)!;
    expect(tokenKey(a)).toBe(tokenKey(b));
  });
});

describe("REPLACE_RANGE", () => {
  const state = {
    lines: ["look at @src/ap now"],
    cursorRow: 0,
    cursorCol: 15,
    scrollOffset: 0,
    historyIndex: 0,
    draft: "",
  };
  it("replaces the token and lands the cursor after the insertion", () => {
    const next = inputReducer(state, {
      type: "REPLACE_RANGE",
      row: 0,
      start: 8,
      end: 15,
      text: "@src/app.ts ",
    });
    expect(next.lines).toEqual(["look at @src/app.ts  now"]);
    expect(next.cursorCol).toBe(20);
  });
  it("clamps a stale range to the line", () => {
    const next = inputReducer(
      { ...state, lines: ["/m"] },
      {
        type: "REPLACE_RANGE",
        row: 0,
        start: 0,
        end: 9,
        text: "/model",
      },
    );
    expect(next.lines).toEqual(["/model"]);
    expect(next.cursorCol).toBe(6);
  });
});

describe("nextDismissedToken", () => {
  const slash = detectComposerToken(["/mo"], 0, 3)!;
  it("keeps the dismissal while the same token is being typed", () => {
    expect(
      nextDismissedToken(tokenKey(slash), detectComposerToken(["/mod"], 0, 4)),
    ).toBe(tokenKey(slash));
  });
  it("forgets the dismissal once the token is gone, so the next / reopens", () => {
    expect(nextDismissedToken(tokenKey(slash), null)).toBeNull();
    expect(
      nextDismissedToken(null, detectComposerToken(["/m"], 0, 2)),
    ).toBeNull();
  });
  it("forgets the dismissal when a different token appears", () => {
    expect(
      nextDismissedToken(tokenKey(slash), detectComposerToken(["hi @s"], 0, 5)),
    ).toBeNull();
  });
});
