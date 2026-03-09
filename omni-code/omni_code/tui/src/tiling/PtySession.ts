/**
 * PtySession — manages a PTY + xterm headless instance.
 *
 * Lifecycle is decoupled from React's component tree so that
 * restructuring the tiling layout doesn't kill running terminals.
 */

// node-pty and @xterm/headless are CommonJS
const { spawn: ptySpawn } = require("node-pty") as typeof import("node-pty");
const { Terminal: XTerminal } = require("@xterm/headless") as typeof import("@xterm/headless");

// --- Rendering types (shared with TerminalView) ---

export type CellStyle = {
  fg?: string;
  bg?: string;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  strikethrough: boolean;
  inverse: boolean;
};

export type Span = { text: string; style: CellStyle };
export type RenderedLine = Span[];

// --- Color utilities ---

const PALETTE_16: string[] = [
  "#000000", "#cd0000", "#00cd00", "#cdcd00", "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
  "#7f7f7f", "#ff0000", "#00ff00", "#ffff00", "#5c5cff", "#ff00ff", "#00ffff", "#ffffff",
];

const INK_COLORS: Record<number, string> = {
  0: "black", 1: "red", 2: "green", 3: "yellow",
  4: "blue", 5: "magenta", 6: "cyan", 7: "white",
  8: "blackBright", 9: "redBright", 10: "greenBright", 11: "yellowBright",
  12: "blueBright", 13: "magentaBright", 14: "cyanBright", 15: "whiteBright",
};

function paletteToHex(index: number): string {
  if (index < 16) return PALETTE_16[index]!;
  if (index < 232) {
    const i = index - 16;
    const r = Math.floor(i / 36) * 51;
    const g = Math.floor((i % 36) / 6) * 51;
    const b = (i % 6) * 51;
    return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
  }
  const v = (index - 232) * 10 + 8;
  return `#${v.toString(16).padStart(2, "0")}${v.toString(16).padStart(2, "0")}${v.toString(16).padStart(2, "0")}`;
}

function rgbToHex(color: number): string {
  const r = (color >> 16) & 0xff;
  const g = (color >> 8) & 0xff;
  const b = color & 0xff;
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
}

function styleKey(s: CellStyle): string {
  return `${s.fg ?? ""}|${s.bg ?? ""}|${s.bold ? 1 : 0}${s.dim ? 1 : 0}${s.italic ? 1 : 0}${s.underline ? 1 : 0}${s.strikethrough ? 1 : 0}${s.inverse ? 1 : 0}`;
}

// --- PtySession ---

export type PtySessionOpts = {
  cols: number;
  rows: number;
  cwd?: string;
  env?: Record<string, string>;
  onUpdate: () => void;
  onExit: (code: number) => void;
};

export class PtySession {
  private pty: ReturnType<typeof ptySpawn>;
  private xterm: InstanceType<typeof XTerminal>;
  private updatePending = false;
  private _onUpdate: () => void;
  private _dead = false;

  constructor(command: string[], opts: PtySessionOpts) {
    this._onUpdate = opts.onUpdate;

    this.xterm = new XTerminal({
      cols: opts.cols,
      rows: opts.rows,
      scrollback: 1000,
      allowProposedApi: true,
    });

    const [cmd, ...args] = command;
    this.pty = ptySpawn(cmd!, args, {
      name: "xterm-256color",
      cols: opts.cols,
      rows: opts.rows,
      cwd: opts.cwd ?? process.cwd(),
      env: { ...process.env, ...opts.env, TERM: "xterm-256color" } as Record<string, string>,
    });

    this.pty.onData((data: string) => {
      this.xterm.write(data, () => this.scheduleUpdate());
    });

    this.pty.onExit(({ exitCode }: { exitCode: number }) => {
      this._dead = true;
      opts.onExit(exitCode);
    });

    // Initial render
    this.scheduleUpdate();
  }

  get dead(): boolean { return this._dead; }

  set onUpdate(cb: () => void) { this._onUpdate = cb; }

  write(data: string): void {
    if (!this._dead) this.pty.write(data);
  }

  resize(cols: number, rows: number): void {
    if (this._dead) return;
    try {
      this.pty.resize(cols, rows);
      this.xterm.resize(cols, rows);
      this.scheduleUpdate();
    } catch {}
  }

  kill(): void {
    if (this._dead) return;
    this._dead = true;
    try { this.pty.kill(); } catch {}
    this.xterm.dispose();
  }

  /** Read the xterm buffer and return styled spans for rendering. */
  readBuffer(cols: number, rows: number): RenderedLine[] {
    const buf = this.xterm.buffer.active;
    const result: RenderedLine[] = [];
    const cell = buf.getNullCell();

    for (let y = 0; y < rows; y++) {
      const line = buf.getLine(buf.viewportY + y);
      if (!line) {
        result.push([{ text: " ".repeat(cols), style: { bold: false, dim: false, italic: false, underline: false, strikethrough: false, inverse: false } }]);
        continue;
      }

      const spans: Span[] = [];
      let currentText = "";
      let currentStyle: CellStyle = { bold: false, dim: false, italic: false, underline: false, strikethrough: false, inverse: false };
      let currentKey = styleKey(currentStyle);

      for (let x = 0; x < cols; x++) {
        const c = line.getCell(x, cell);
        if (!c) { currentText += " "; continue; }
        if (c.getWidth() === 0) continue;

        let fg: string | undefined;
        if (c.isFgPalette()) {
          const idx = c.getFgColor();
          fg = INK_COLORS[idx] ?? paletteToHex(idx);
        } else if (c.isFgRGB()) {
          fg = rgbToHex(c.getFgColor());
        }

        let bg: string | undefined;
        if (c.isBgPalette()) {
          const idx = c.getBgColor();
          bg = INK_COLORS[idx] ?? paletteToHex(idx);
        } else if (c.isBgRGB()) {
          bg = rgbToHex(c.getBgColor());
        }

        const style: CellStyle = {
          fg, bg,
          bold: !!c.isBold(), dim: !!c.isDim(), italic: !!c.isItalic(),
          underline: !!c.isUnderline(), strikethrough: !!c.isStrikethrough(), inverse: !!c.isInverse(),
        };

        const key = styleKey(style);
        if (key !== currentKey) {
          if (currentText) spans.push({ text: currentText, style: currentStyle });
          currentText = c.getChars() || " ";
          currentStyle = style;
          currentKey = key;
        } else {
          currentText += c.getChars() || " ";
        }
      }
      if (currentText) spans.push({ text: currentText, style: currentStyle });
      result.push(spans);
    }

    return result;
  }

  private scheduleUpdate(): void {
    if (this.updatePending) return;
    this.updatePending = true;
    setTimeout(() => {
      this.updatePending = false;
      this._onUpdate();
    }, 16); // ~60fps cap
  }
}
