import stringWidth from "string-width";

export interface InputRow {
  line: number;
  start: number;
  end: number;
  text: string;
}
const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

/** Logical offsets remain code points; wrapping uses terminal cells and graphemes. */
export function layoutInput(
  lines: string[],
  cursorLine: number,
  cursorCol: number,
  columns: number,
) {
  const width = Math.max(1, columns);
  const rows: InputRow[] = [];
  let cursorRow = 0;
  let cursorCell = 0;
  for (let line = 0; line < lines.length; line++) {
    let start = 0;
    let end = 0;
    let cells = 0;
    let parts: string[] = [];
    const flush = () => {
      rows.push({ line, start, end, text: parts.join("") });
      start = end;
      cells = 0;
      parts = [];
    };
    const text = lines[line] + (line === cursorLine ? " " : "");
    for (const { segment } of segmenter.segment(text)) {
      const size =
        segment.length === 1 && segment >= " " && segment <= "~"
          ? 1
          : stringWidth(segment);
      if (cells > 0 && cells + size > width) flush();
      const points = Array.from(segment).length;
      if (line === cursorLine && cursorCol >= end && cursorCol < end + points) {
        cursorRow = rows.length;
        cursorCell = cells;
      }
      parts.push(segment);
      cells += size;
      end += points;
    }
    flush();
  }
  return { rows, cursorRow, cursorCell };
}

export function columnAtCell(row: InputRow, cell: number): number {
  let col = row.start;
  let width = 0;
  for (const { segment } of segmenter.segment(row.text)) {
    const size = stringWidth(segment);
    if (width + size > cell) break;
    width += size;
    col += Array.from(segment).length;
  }
  return col;
}
