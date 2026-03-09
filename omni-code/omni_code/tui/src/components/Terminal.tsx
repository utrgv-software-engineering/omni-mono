import React, { useCallback, useEffect, useRef, useState } from "react";
import { Box, Text, useStdin } from "ink";
import { useTerminalSize } from "./Panel.js";

// node-pty and @xterm/headless are CommonJS — use require via the banner shim
const { spawn: ptySpawn } = require("node-pty") as typeof import("node-pty");
const { Terminal: XTerminal } = require("@xterm/headless") as typeof import("@xterm/headless");

// ANSI 256-color palette (0-15 are the standard terminal colors)
const PALETTE_16: string[] = [
  "#000000", "#cd0000", "#00cd00", "#cdcd00", "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
  "#7f7f7f", "#ff0000", "#00ff00", "#ffff00", "#5c5cff", "#ff00ff", "#00ffff", "#ffffff",
];

// Named color map for Ink's color prop (basic 8 + bright 8)
const INK_COLORS: Record<number, string> = {
  0: "black", 1: "red", 2: "green", 3: "yellow",
  4: "blue", 5: "magenta", 6: "cyan", 7: "white",
  8: "blackBright", 9: "redBright", 10: "greenBright", 11: "yellowBright",
  12: "blueBright", 13: "magentaBright", 14: "cyanBright", 15: "whiteBright",
};

function paletteToHex(index: number): string {
  if (index < 16) return PALETTE_16[index]!;
  if (index < 232) {
    // 6x6x6 color cube
    const i = index - 16;
    const r = Math.floor(i / 36) * 51;
    const g = Math.floor((i % 36) / 6) * 51;
    const b = (i % 6) * 51;
    return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
  }
  // Grayscale ramp
  const v = (index - 232) * 10 + 8;
  return `#${v.toString(16).padStart(2, "0")}${v.toString(16).padStart(2, "0")}${v.toString(16).padStart(2, "0")}`;
}

function rgbToHex(color: number): string {
  const r = (color >> 16) & 0xff;
  const g = (color >> 8) & 0xff;
  const b = color & 0xff;
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
}

type CellStyle = {
  fg?: string;
  bg?: string;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  strikethrough: boolean;
  inverse: boolean;
};

function styleKey(s: CellStyle): string {
  return `${s.fg ?? ""}|${s.bg ?? ""}|${s.bold ? 1 : 0}${s.dim ? 1 : 0}${s.italic ? 1 : 0}${s.underline ? 1 : 0}${s.strikethrough ? 1 : 0}${s.inverse ? 1 : 0}`;
}

type Span = {
  text: string;
  style: CellStyle;
};

type RenderedLine = Span[];

type Props = {
  /** Command to run, e.g. ["bash"] or ["ssh", "user@host"] */
  command: string[];
  /** Environment variables to pass */
  env?: Record<string, string>;
  /** Working directory */
  cwd?: string;
  /** Cols/rows override (otherwise uses available space) */
  cols?: number;
  rows?: number;
  /** Called when the PTY process exits */
  onExit?: (code: number) => void;
  /** Whether this terminal is focused and receives input */
  focused?: boolean;
  /** Called when the user presses Ctrl-] to release focus back to the parent */
  onFocusRequest?: () => void;
};

export function Terminal({
  command,
  env,
  cwd,
  cols: colsProp,
  rows: rowsProp,
  onExit,
  focused = true,
  onFocusRequest,
}: Props) {
  const { cols: termCols, rows: termRows } = useTerminalSize();
  const { stdin, setRawMode } = useStdin();

  // Default size fills available space (sidebar=26 + border=2, panel border=2, title/status bars=4)
  const cols = colsProp ?? Math.max(20, termCols - 26 - 4);
  const rows = rowsProp ?? Math.max(5, termRows - 4 - 3); // title bar + status bar + panel border + title

  const [lines, setLines] = useState<RenderedLine[]>([]);
  const [cursorPos, setCursorPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const ptyRef = useRef<ReturnType<typeof ptySpawn> | null>(null);
  const xtermRef = useRef<InstanceType<typeof XTerminal> | null>(null);
  const rafRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stdinListenerRef = useRef<((data: Buffer) => void) | null>(null);

  // Read the xterm buffer and convert to styled spans
  const readBuffer = useCallback(() => {
    const xterm = xtermRef.current;
    if (!xterm) return;

    const buf = xterm.buffer.active;
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
        if (!c) {
          currentText += " ";
          continue;
        }

        const width = c.getWidth();
        if (width === 0) continue; // Skip trailing half of wide char

        // Extract style
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
          fg,
          bg,
          bold: !!c.isBold(),
          dim: !!c.isDim(),
          italic: !!c.isItalic(),
          underline: !!c.isUnderline(),
          strikethrough: !!c.isStrikethrough(),
          inverse: !!c.isInverse(),
        };

        const key = styleKey(style);
        if (key !== currentKey) {
          if (currentText) {
            spans.push({ text: currentText, style: currentStyle });
          }
          currentText = c.getChars() || " ";
          currentStyle = style;
          currentKey = key;
        } else {
          currentText += c.getChars() || " ";
        }
      }

      if (currentText) {
        spans.push({ text: currentText, style: currentStyle });
      }
      result.push(spans);
    }

    setLines(result);
    setCursorPos({ x: buf.cursorX, y: buf.cursorY });
  }, [cols, rows]);

  // Schedule a throttled buffer read
  const scheduleRead = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = setTimeout(() => {
      rafRef.current = null;
      readBuffer();
    }, 16); // ~60fps cap
  }, [readBuffer]);

  // Spawn PTY and xterm
  useEffect(() => {
    const [cmd, ...args] = command;
    if (!cmd) return;

    const xterm = new XTerminal({ cols, rows, scrollback: 1000, allowProposedApi: true });
    xtermRef.current = xterm;

    const pty = ptySpawn(cmd, args, {
      name: "xterm-256color",
      cols,
      rows,
      cwd: cwd ?? process.cwd(),
      env: { ...process.env, ...env, TERM: "xterm-256color" } as Record<string, string>,
    });
    ptyRef.current = pty;

    // Pipe PTY output -> xterm
    pty.onData((data: string) => {
      xterm.write(data, scheduleRead);
    });

    pty.onExit(({ exitCode }: { exitCode: number }) => {
      onExit?.(exitCode);
    });

    // Initial render
    scheduleRead();

    return () => {
      if (rafRef.current) clearTimeout(rafRef.current);
      rafRef.current = null;
      xtermRef.current = null;
      ptyRef.current = null;
      try { pty.kill(); } catch {}
      xterm.dispose();
    };
  }, [command.join(" "), cwd, env ? JSON.stringify(env) : ""]);

  // Resize PTY when dimensions change
  useEffect(() => {
    const pty = ptyRef.current;
    const xterm = xtermRef.current;
    if (!pty || !xterm) return;
    try {
      pty.resize(cols, rows);
      xterm.resize(cols, rows);
      scheduleRead();
    } catch {}
  }, [cols, rows, scheduleRead]);

  // Stable ref for onFocusRequest so the raw-mode effect doesn't churn
  // every time the parent re-renders with a new closure.
  const focusRequestRef = useRef(onFocusRequest);
  useEffect(() => { focusRequestRef.current = onFocusRequest; }, [onFocusRequest]);

  // Forward raw stdin to PTY when focused.
  // Intercept Ctrl-o or Ctrl-] to release focus.
  //
  // IMPORTANT: Ink calls stdin.setEncoding('utf8') in handleSetRawMode,
  // so data events deliver strings, not Buffers. We must compare against
  // string characters, not numeric byte values.
  useEffect(() => {
    if (!focused || !stdin) return;

    setRawMode(true);

    const releaseFocus = () => {
      focusRequestRef.current?.();
    };

    const listener = (data: string | Buffer) => {
      const str = typeof data === "string" ? data : data.toString("utf-8");

      // Ctrl-] — release focus
      if (str === "\x1d") {
        releaseFocus();
        return;
      }
      const pty = ptyRef.current;
      if (pty) {
        pty.write(str);
      }
    };
    stdinListenerRef.current = listener;
    stdin.on("data", listener);

    return () => {
      stdin.off("data", listener);
      stdinListenerRef.current = null;
      try { setRawMode(false); } catch {}
    };
  }, [focused, stdin, setRawMode]);

  return (
    <Box flexDirection="column" width={cols} height={rows}>
      {lines.map((spans, y) => (
        <Box key={y}>
          {spans.map((span, i) => (
            <Text
              key={i}
              color={span.style.fg}
              backgroundColor={span.style.bg}
              bold={span.style.bold}
              dimColor={span.style.dim}
              italic={span.style.italic}
              underline={span.style.underline}
              strikethrough={span.style.strikethrough}
              inverse={span.style.inverse}
            >
              {span.text}
            </Text>
          ))}
        </Box>
      ))}
    </Box>
  );
}
