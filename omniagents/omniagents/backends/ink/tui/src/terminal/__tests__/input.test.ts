import { afterEach, describe, expect, it, vi } from "vitest";
import { TerminalInputParser } from "../input.js";
import type { Key } from "../../hooks/useKeypress.js";

const parse = (chunks: string[]) => {
  const events: Array<{ text: string; key: Key }> = [];
  const parser = new TerminalInputParser((text, key) =>
    events.push({ text, key }),
  );
  for (const chunk of chunks) parser.feed(chunk);
  parser.dispose();
  return events;
};
afterEach(() => {
  vi.useRealTimers();
});

describe("terminal input framing", () => {
  it("keeps adjacent text and special keys in order", () => {
    const events = parse(["abc\x1b[DX\x7f\x7f\r"]);
    expect(events.map((e) => e.text)).toEqual(["abc", "", "X", "", "", ""]);
    expect(events[1].key.leftArrow).toBe(true);
    expect(events[3].key.backspace).toBe(true);
    expect(events[4].key.backspace).toBe(true);
    expect(events[5].key.return).toBe(true);
  });

  it("handles every split point in a modified arrow sequence", () => {
    const sequence = "\x1b[1;5D";
    for (let i = 1; i < sequence.length; i++) {
      const events = parse([sequence.slice(0, i), sequence.slice(i) + "X"]);
      expect(events).toHaveLength(2);
      expect(events[0].key).toMatchObject({ leftArrow: true, ctrl: true });
      expect(events[1].text).toBe("X");
    }
  });

  it("preserves multiline paste across every delimiter split", () => {
    const sequence = "\x1b[200~a\r\nb\x03\x1b[201~";
    for (let i = 1; i < sequence.length; i++) {
      const events = parse([sequence.slice(0, i), sequence.slice(i)]);
      expect(events).toHaveLength(1);
      expect(events[0]).toMatchObject({
        text: "a\r\nb\x03",
        key: { paste: true, ctrl: false, return: false },
      });
    }
  });

  it("distinguishes Delete from backspace and supports modified Enter", () => {
    const events = parse(["\x7f\x1b[3~\x1b[13;2u\x1b[27;5;13~"]);
    expect(events[0].key.backspace).toBe(true);
    expect(events[1].key.delete).toBe(true);
    expect(events[2].key).toMatchObject({ return: true, shift: true });
    expect(events[3].key).toMatchObject({ return: true, ctrl: true });
  });

  it("preserves control and alt chords", () => {
    expect(
      parse(["\x03\x1d\x1b1"]).map((e) => [e.text, e.key.ctrl, e.key.meta]),
    ).toEqual([
      ["c", true, false],
      ["]", true, false],
      ["1", false, true],
    ]);
  });

  it("coalesces CRLF Enter including across reads", () => {
    expect(parse(["\r", "\n"]).filter((e) => e.key.return)).toHaveLength(1);
  });

  it("filters split mouse reports without consuming following text", () => {
    expect(parse(["\x1b[<0;", "5;10Mhello"]).map((e) => e.text)).toEqual([
      "hello",
    ]);
  });

  it("resolves standalone Escape and cancels its timer on cleanup", () => {
    vi.useFakeTimers();
    const emit = vi.fn();
    const parser = new TerminalInputParser(emit);
    parser.feed("\x1b");
    expect(emit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(50);
    expect(emit).toHaveBeenCalledWith(
      "",
      expect.objectContaining({ escape: true }),
    );
    parser.feed("\x1b");
    parser.dispose();
    vi.runAllTimers();
    expect(emit).toHaveBeenCalledTimes(1);
  });
});
