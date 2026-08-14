// Ink keypress → PTY byte translation. Pure and unit-testable.
//
// The docked terminal receives parsed Ink keys (we sit inside the
// KeypressContext like every other surface) and reconstructs the byte
// sequence a real terminal would have sent. Covers the line-oriented
// world (shells, REPLs, dev servers) fully.
//
// This runs only while the terminal screen is *captured* — full-screen
// apps (vim, htop, less) therefore receive keys exactly while captured
// and stop receiving them the moment you release with ctrl+], which is
// how Neovim's own :terminal behaves.

export type InkKey = {
  escape?: boolean;
  return?: boolean;
  backspace?: boolean;
  delete?: boolean;
  tab?: boolean;
  upArrow?: boolean;
  downArrow?: boolean;
  leftArrow?: boolean;
  rightArrow?: boolean;
  pageUp?: boolean;
  pageDown?: boolean;
  ctrl?: boolean;
  meta?: boolean;
  shift?: boolean;
};

/**
 * The byte string to write to the PTY for one keypress, or null when
 * the key is not the terminal's to consume (detach chord, scrollback
 * keys — the caller owns those).
 */
// Terminal reports that are OURS (or nobody's), never the shell's:
// SGR mouse (\x1b[<b;x;yM/m), legacy X10 mouse (\x1b[M..), focus
// in/out (\x1b[I / \x1b[O). Mouse tracking is suspended while the
// terminal screen owns the surface, but reports race through mode
// transitions — forwarded, the shell echoes them as typed garbage.
const TERMINAL_REPORT = /^\[(?:<\d+;\d+;\d+[Mm]|M[\s\S]{0,3}|I|O)$/;

export function keyToBytes(input: string, key: InkKey): string | null {
  // Ctrl+] is the detach chord (the telnet convention) — never
  // forwarded. PgUp/PgDn belong to our scrollback.
  if ((key.ctrl && input === "]") || input === "\u001d") return null;
  if (key.pageUp || key.pageDown) return null;
  if (TERMINAL_REPORT.test(input)) return null;

  if (key.return) return "\r";
  if (key.backspace || key.delete) return "\x7f";
  if (key.tab) return key.shift ? "\x1b[Z" : "\t";
  if (key.upArrow) return "\x1b[A";
  if (key.downArrow) return "\x1b[B";
  if (key.rightArrow) return "\x1b[C";
  if (key.leftArrow) return "\x1b[D";

  if (key.ctrl && input.length === 1) {
    const code = input.toLowerCase().charCodeAt(0);
    if (code >= 97 && code <= 122) {
      return String.fromCharCode(code - 96); // ctrl+a..z -> 0x01..0x1a
    }
    return null;
  }

  // Plain Escape arrives as meta with no input (Ink's meta-for-escape).
  if (key.escape) return "\x1b";
  if (key.meta && input.length > 0) return `\x1b${input}`; // alt+key

  if (input.length > 0) return input; // printable chars, incl. paste

  return null;
}
