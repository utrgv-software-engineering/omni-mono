/**
 * A minimal terminal grid: enough of VT100 to replay what Ink's
 * alternate-buffer writer emits (absolute and relative cursor moves,
 * erase line / screen, CR / LF with scrolling and the tty's LF -> CR LF,
 * SGR and private modes ignored), so a test can see the screen the user would see.
 */
export class ScreenGrid {
  private grid: string[][];
  private row = 0;
  private col = 0;

  constructor(
    private columns: number,
    private rows: number,
  ) {
    this.grid = this.blank(rows);
  }

  private blank(n: number) {
    return Array.from({ length: n }, () => Array(this.columns).fill(" "));
  }

  resize(columns: number, rows: number) {
    this.columns = columns;
    this.rows = rows;
    const next = this.blank(rows);
    for (let r = 0; r < Math.min(rows, this.grid.length); r++) {
      for (let c = 0; c < Math.min(columns, this.grid[r]!.length); c++) {
        next[r]![c] = this.grid[r]![c]!;
      }
    }
    this.grid = next;
    this.row = Math.min(this.row, rows - 1);
    this.col = Math.min(this.col, columns - 1);
  }

  feed(data: string) {
    const re = /\x1b\[([?0-9;]*)([A-Za-z])|\x1b[78]|\r|\n|[^\x1b\r\n]/gu;
    for (const m of data.matchAll(re)) {
      const token = m[0];
      if (m[2] !== undefined) this.csi(m[1] ?? "", m[2]);
      else if (token === "\r") this.col = 0;
      else if (token === "\n") {
        // The tty's ONLCR turns a bare LF into CR LF on the way out.
        this.col = 0;
        this.lineFeed();
      } else if (!token.startsWith("\x1b")) this.put(token);
    }
  }

  private lineFeed() {
    if (this.row === this.rows - 1) {
      this.grid.shift();
      this.grid.push(Array(this.columns).fill(" "));
    } else {
      this.row++;
    }
  }

  private put(ch: string) {
    if (this.col >= this.columns) return; // autowrap is off (DECAWM)
    this.grid[this.row]![this.col] = ch;
    this.col++;
  }

  private csi(params: string, final: string) {
    if (params.startsWith("?")) return; // private modes
    const nums = params.split(";").map((p) => (p === "" ? NaN : Number(p)));
    const n = (i: number, d: number) =>
      Number.isNaN(nums[i]!) || nums[i] === undefined ? d : nums[i]!;
    const clampRow = (r: number) => Math.max(0, Math.min(this.rows - 1, r));
    switch (final) {
      case "H":
      case "f":
        this.row = clampRow(n(0, 1) - 1);
        this.col = Math.max(0, n(1, 1) - 1);
        break;
      case "G":
        this.col = Math.max(0, n(0, 1) - 1);
        break;
      case "A":
        this.row = clampRow(this.row - n(0, 1));
        break;
      case "B":
        this.row = clampRow(this.row + n(0, 1));
        break;
      case "C":
        this.col = Math.min(this.columns - 1, this.col + n(0, 1));
        break;
      case "D":
        this.col = Math.max(0, this.col - n(0, 1));
        break;
      case "E":
        this.row = clampRow(this.row + n(0, 1));
        this.col = 0;
        break;
      case "F":
        this.row = clampRow(this.row - n(0, 1));
        this.col = 0;
        break;
      case "K": {
        const mode = n(0, 0);
        const line = this.grid[this.row]!;
        const [from, to] =
          mode === 0
            ? [this.col, this.columns]
            : mode === 1
              ? [0, this.col + 1]
              : [0, this.columns];
        for (let c = from; c < to; c++) line[c] = " ";
        break;
      }
      case "J": {
        const mode = n(0, 0);
        if (mode === 2 || mode === 3) this.grid = this.blank(this.rows);
        else if (mode === 0) {
          this.csi("0", "K");
          for (let r = this.row + 1; r < this.rows; r++)
            this.grid[r] = Array(this.columns).fill(" ");
        }
        break;
      }
      default:
        break; // SGR (m) and anything else that does not move or paint
    }
  }

  /** The visible rows, trailing spaces trimmed. */
  lines(): string[] {
    return this.grid.map((r) => r.join("").trimEnd());
  }
}
