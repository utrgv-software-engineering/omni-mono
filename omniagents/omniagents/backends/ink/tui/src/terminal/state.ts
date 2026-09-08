// The terminal-emulation seam. The UI talks to `TerminalState` only;
// which emulator maintains the grid is an implementation detail. The
// default implementation rides @xterm/headless (pure JS, bundles like
// any dep); libghostty-vt-node satisfies the same seam if we ever want
// Ghostty's VT core behind it (native addon — packaging work).

import { Terminal } from "@xterm/headless";
import type { IBufferCell, IBufferLine } from "@xterm/headless";

export interface TerminalState {
  inputModes?(): {
    bracketedPasteMode: boolean;
    applicationCursorKeysMode: boolean;
  };
  feed(data: string | Uint8Array): void;
  resize(cols: number, rows: number): void;
  /** Styled (ANSI SGR) text for buffer rows [start, start+count). */
  readLines(start: number, count: number): string[];
  /** Total buffer rows: scrollback + viewport. */
  lineCount(): number;
  /** Index of the viewport's first row within the buffer. */
  baseY(): number;
  altScreenActive(): boolean;
  cols(): number;
  rows(): number;
  /** Batched change notification; returns unsubscribe. */
  onUpdate(listener: () => void): () => void;
  dispose(): void;
}

// ----- SGR reconstruction -----
//
// xterm's buffer stores per-cell attributes; Ink renders raw ANSI in
// Text just fine. Rebuild minimal SGR runs: emit a fresh sequence only
// when the style changes from the previous cell.

function styleKey(cell: IBufferCell): string {
  // Attribute getters return 0/1, not booleans — normalize so the
  // default-style key compares reliably.
  return [
    !!cell.isBold(),
    !!cell.isDim(),
    !!cell.isItalic(),
    !!cell.isUnderline(),
    !!cell.isInverse(),
    !!cell.isStrikethrough(),
    cell.isFgDefault()
      ? "d"
      : cell.isFgRGB()
        ? `r${cell.getFgColor()}`
        : `p${cell.getFgColor()}`,
    cell.isBgDefault()
      ? "d"
      : cell.isBgRGB()
        ? `r${cell.getBgColor()}`
        : `p${cell.getBgColor()}`,
  ].join("|");
}

function sgrFor(cell: IBufferCell): string {
  const parts: string[] = ["0"];
  if (cell.isBold()) parts.push("1");
  if (cell.isDim()) parts.push("2");
  if (cell.isItalic()) parts.push("3");
  if (cell.isUnderline()) parts.push("4");
  if (cell.isInverse()) parts.push("7");
  if (cell.isStrikethrough()) parts.push("9");
  if (!cell.isFgDefault()) {
    const color = cell.getFgColor();
    if (cell.isFgRGB()) {
      parts.push(
        "38",
        "2",
        String((color >> 16) & 0xff),
        String((color >> 8) & 0xff),
        String(color & 0xff),
      );
    } else if (color < 8) {
      parts.push(String(30 + color));
    } else if (color < 16) {
      parts.push(String(90 + color - 8));
    } else {
      parts.push("38", "5", String(color));
    }
  }
  if (!cell.isBgDefault()) {
    const color = cell.getBgColor();
    if (cell.isBgRGB()) {
      parts.push(
        "48",
        "2",
        String((color >> 16) & 0xff),
        String((color >> 8) & 0xff),
        String(color & 0xff),
      );
    } else if (color < 8) {
      parts.push(String(40 + color));
    } else if (color < 16) {
      parts.push(String(100 + color - 8));
    } else {
      parts.push("48", "5", String(color));
    }
  }
  return `\x1b[${parts.join(";")}m`;
}

const DEFAULT_STYLE_KEY = "false|false|false|false|false|false|d|d";

function lineToAnsi(
  line: IBufferLine,
  cols: number,
  cell: IBufferCell,
): string {
  let out = "";
  let lastKey = DEFAULT_STYLE_KEY;
  let styled = false;
  for (let x = 0; x < cols; x += 1) {
    line.getCell(x, cell);
    if (cell.getWidth() === 0) continue; // wide-char continuation
    const chars = cell.getChars() || " ";
    const key = styleKey(cell);
    if (key !== lastKey) {
      out += sgrFor(cell);
      lastKey = key;
      styled = true;
    }
    out += chars;
  }
  if (styled) out += "\x1b[0m";
  // Trailing blank cells are structural, not content.
  return styled
    ? out.replace(/ +\x1b\[0m$/, "\x1b[0m")
    : out.replace(/ +$/, "");
}

export class XtermTerminalState implements TerminalState {
  private term: Terminal;
  private listeners = new Set<() => void>();
  private pending = false;
  private disposables: Array<{ dispose(): void }> = [];

  constructor(cols: number, rows: number, scrollback = 2000) {
    this.term = new Terminal({
      cols,
      rows,
      scrollback,
      allowProposedApi: true,
    });
    this.disposables.push(
      this.term.onWriteParsed(() => this.notify()),
      this.term.onResize(() => this.notify()),
    );
  }

  private notify(): void {
    // Coalesce bursts: one update per macrotask.
    if (this.pending) return;
    this.pending = true;
    setTimeout(() => {
      this.pending = false;
      for (const listener of this.listeners) listener();
    }, 16);
  }

  inputModes() {
    return this.term.modes;
  }

  feed(data: string | Uint8Array): void {
    this.term.write(data);
  }

  resize(cols: number, rows: number): void {
    if (cols === this.term.cols && rows === this.term.rows) return;
    this.term.resize(cols, rows);
  }

  readLines(start: number, count: number): string[] {
    const buffer = this.term.buffer.active;
    const cell = buffer.getNullCell();
    const out: string[] = [];
    for (let y = start; y < start + count; y += 1) {
      const line = buffer.getLine(y);
      out.push(line ? lineToAnsi(line, this.term.cols, cell) : "");
    }
    return out;
  }

  lineCount(): number {
    return this.term.buffer.active.length;
  }

  baseY(): number {
    return this.term.buffer.active.baseY;
  }

  altScreenActive(): boolean {
    return this.term.buffer.active.type === "alternate";
  }

  cols(): number {
    return this.term.cols;
  }

  rows(): number {
    return this.term.rows;
  }

  onUpdate(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  dispose(): void {
    for (const d of this.disposables) d.dispose();
    this.listeners.clear();
    this.term.dispose();
  }
}
