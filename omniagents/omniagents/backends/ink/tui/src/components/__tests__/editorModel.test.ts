import { describe, expect, it } from "vitest";
import {
  applyEdit,
  fromText,
  toText,
  type EditorBuffer,
} from "../editor-model.js";

const VIEW = 10;

const type = (buf: EditorBuffer, text: string) =>
  applyEdit(buf, text, {}, VIEW);

describe("editor buffer", () => {
  it("inserts text and marks dirty", () => {
    let buf = fromText("hello");
    expect(buf.dirty).toBe(false);
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW);
    buf = type(buf, "i");
    expect(toText(buf)).toBe("hiello");
    expect(buf.dirty).toBe(true);
    expect(buf.col).toBe(2);
  });

  it("splits and joins lines with enter/backspace", () => {
    let buf = fromText("ab");
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW);
    buf = applyEdit(buf, "", { return: true }, VIEW);
    expect(toText(buf)).toBe("a\nb");
    expect([buf.row, buf.col]).toEqual([1, 0]);
    buf = applyEdit(buf, "", { backspace: true }, VIEW);
    expect(toText(buf)).toBe("ab");
    expect([buf.row, buf.col]).toEqual([0, 1]);
  });

  it("pastes multi-line text with CRLF normalization", () => {
    let buf = fromText("xy");
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW);
    buf = type(buf, "1\r\n2\n3");
    expect(toText(buf)).toBe("x1\n2\n3y");
    expect([buf.row, buf.col]).toEqual([2, 1]);
  });

  it("clamps column when moving across lines of different length", () => {
    let buf = fromText("long line\nab");
    buf = { ...buf, col: 8 };
    buf = applyEdit(buf, "", { downArrow: true }, VIEW);
    expect([buf.row, buf.col]).toEqual([1, 2]);
  });

  it("left/right cross line boundaries", () => {
    let buf = fromText("a\nb");
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW); // after 'a'
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW); // start of 'b'
    expect([buf.row, buf.col]).toEqual([1, 0]);
    buf = applyEdit(buf, "", { leftArrow: true }, VIEW);
    expect([buf.row, buf.col]).toEqual([0, 1]);
  });

  it("keeps the cursor inside the viewport", () => {
    let buf = fromText(
      Array.from({ length: 40 }, (_, i) => `l${i}`).join("\n"),
    );
    for (let i = 0; i < 25; i += 1) {
      buf = applyEdit(buf, "", { downArrow: true }, VIEW);
    }
    expect(buf.row).toBe(25);
    expect(buf.top).toBe(16); // row visible at bottom of the window
    buf = applyEdit(buf, "", { pageUp: true }, VIEW);
    expect(buf.row).toBe(16);
    expect(buf.top).toBeLessThanOrEqual(16);
  });

  it("forward-deletes and joins on delete at EOL", () => {
    let buf = fromText("ab\ncd");
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW);
    buf = applyEdit(buf, "", { rightArrow: true }, VIEW);
    buf = applyEdit(buf, "", { delete: true }, VIEW);
    expect(toText(buf)).toBe("abcd");
  });

  it("tab indents two spaces", () => {
    let buf = fromText("x");
    buf = applyEdit(buf, "", { tab: true }, VIEW);
    expect(toText(buf)).toBe("  x");
  });
});
