import type { Key } from "../hooks/useKeypress.js";

const emptyKey = (): Key => ({
  upArrow: false,
  downArrow: false,
  leftArrow: false,
  rightArrow: false,
  pageDown: false,
  pageUp: false,
  return: false,
  escape: false,
  ctrl: false,
  shift: false,
  tab: false,
  backspace: false,
  delete: false,
  meta: false,
});

/** Frame terminal input independently of OS/readable chunk boundaries. */
export class TerminalInputParser {
  private pending = "";
  private paste: string[] | null = null;
  private escapeTimer: ReturnType<typeof setTimeout> | undefined;
  private skipLF = false;

  constructor(private emit: (input: string, key: Key) => void) {}

  dispose(): void {
    clearTimeout(this.escapeTimer);
    this.pending = "";
    this.paste = null;
  }

  feed(chunk: string): void {
    clearTimeout(this.escapeTimer);
    this.pending += chunk;
    while (this.pending) {
      if (this.paste) {
        const end = "\x1b[201~";
        const index = this.pending.indexOf(end);
        if (index >= 0) {
          this.paste.push(this.pending.slice(0, index));
          const text = this.paste.join("");
          this.paste = null;
          this.pending = this.pending.slice(index + end.length);
          this.emit(text, { ...emptyKey(), paste: true });
          continue;
        }
        // Keep only a possible delimiter prefix across reads, not the entire paste.
        let suffix = Math.min(end.length - 1, this.pending.length);
        while (suffix && !end.startsWith(this.pending.slice(-suffix))) suffix--;
        this.paste.push(this.pending.slice(0, this.pending.length - suffix));
        this.pending = suffix ? this.pending.slice(-suffix) : "";
        return;
      }
      if (this.skipLF) {
        this.skipLF = false;
        if (this.pending[0] === "\n") {
          this.pending = this.pending.slice(1);
          continue;
        }
      }
      if (this.pending.startsWith("\x1b[200~")) {
        this.pending = this.pending.slice(6);
        this.paste = [];
        continue;
      }
      if (this.pending[0] === "\x1b") {
        if (this.pending.length === 1) {
          this.escapeTimer = setTimeout(() => {
            this.pending = "";
            this.emit("", { ...emptyKey(), escape: true });
          }, 50);
          return;
        }
        const introducer = this.pending[1];
        if (introducer === "[" || introducer === "O") {
          // Older Cygwin/Linux-console function keys use CSI [ A..E.
          if (this.pending.startsWith("\x1b[[")) {
            if (this.pending.length < 4) return;
            this.pending = this.pending.slice(4);
            continue;
          }
          // X10 mouse reports include three bytes after CSI M.
          if (this.pending.startsWith("\x1b[M")) {
            if (this.pending.length < 6) return;
            this.pending = this.pending.slice(6);
            continue;
          }
          const match = /^\x1b(?:\[|O)([0-?]*)([ -/]*)([@-~])/.exec(
            this.pending,
          );
          if (!match) {
            if (this.pending.length > 1024) this.pending = "";
            return;
          }
          this.pending = this.pending.slice(match[0].length);
          this.sequence(match[1], match[3]);
          continue;
        }
        // Terminal replies/control strings must never turn into editable text.
        if ("]PX^_".includes(introducer)) {
          const match = /^\x1b[\]PX^_][\s\S]*?(?:\x07|\x1b\\)/.exec(
            this.pending,
          );
          if (!match) {
            if (this.pending.length > 4096) this.pending = "";
            return;
          }
          this.pending = this.pending.slice(match[0].length);
          continue;
        }
        const char = String.fromCodePoint(this.pending.codePointAt(1)!);
        this.pending = this.pending.slice(1 + char.length);
        this.character(char, true);
        continue;
      }
      const printable = /^[^\x00-\x1f\x7f]+/.exec(this.pending);
      if (printable) {
        this.pending = this.pending.slice(printable[0].length);
        this.emit(printable[0], {
          ...emptyKey(),
          shift: /^[A-Z]$/.test(printable[0]),
        });
      } else {
        const char = this.pending[0];
        this.pending = this.pending.slice(1);
        this.skipLF = char === "\r";
        this.character(char);
      }
    }
  }

  private character(char: string, meta = false, modifier = 1): void {
    const key = emptyKey();
    key.shift = !!((modifier - 1) & 1);
    key.meta = meta || !!((modifier - 1) & 2);
    key.ctrl = !!((modifier - 1) & 4);
    if (char === "\r" || char === "\n") key.return = true;
    else if (char === "\x1b") key.escape = true;
    else if (char === "\x7f" || char === "\b") key.backspace = true;
    else if (char === "\t") key.tab = true;
    else if (char.charCodeAt(0) < 32) {
      key.ctrl = true;
      this.emit(
        String.fromCharCode(char.charCodeAt(0) + 64).toLowerCase(),
        key,
      );
      return;
    } else {
      key.shift ||= /^[A-Z]$/.test(char);
      this.emit(key.ctrl ? char.toLowerCase() : char, key);
      return;
    }
    this.emit("", key);
  }

  private sequence(params: string, final: string): void {
    const values = params.split(";");
    const modifier = Number(values[1]?.split(":")[0] ?? 1);
    if (final === "u" || (final === "~" && values[0] === "27")) {
      if (values[1]?.split(":")[1] === "3") return; // Kitty key release
      const code = Number(final === "u" ? values[0] : values[2]);
      if (code >= 0 && code <= 0x10ffff)
        this.character(String.fromCodePoint(code), false, modifier);
      return;
    }
    const key = emptyKey();
    key.shift = !!((modifier - 1) & 1);
    key.meta = !!((modifier - 1) & 2);
    key.ctrl = !!((modifier - 1) & 4);
    const names: Record<string, keyof Key> = {
      A: "upArrow",
      B: "downArrow",
      C: "rightArrow",
      D: "leftArrow",
      H: "home",
      F: "end",
      Z: "tab",
    };
    const tilde: Record<string, keyof Key> = {
      "1": "home",
      "3": "delete",
      "4": "end",
      "5": "pageUp",
      "6": "pageDown",
      "7": "home",
      "8": "end",
    };
    const name = final === "~" ? tilde[values[0]] : names[final];
    if (!name) return; // Includes mouse reports and unsupported terminal replies.
    Object.assign(key, { [name]: true });
    if (final === "Z") key.shift = true;
    this.emit("", key);
  }
}
