// Pure text-buffer logic for the Files screen's inline editor. Same
// split as ./review-model: every keystroke is a pure transition,
// unit-testable without Ink.

export type EditorBuffer = {
  lines: string[];
  row: number;
  col: number;
  /** First visible row of the viewport. */
  top: number;
  dirty: boolean;
};

export type EditorKey = {
  upArrow?: boolean;
  downArrow?: boolean;
  leftArrow?: boolean;
  rightArrow?: boolean;
  pageUp?: boolean;
  pageDown?: boolean;
  return?: boolean;
  backspace?: boolean;
  delete?: boolean;
  tab?: boolean;
  ctrl?: boolean;
  meta?: boolean;
  shift?: boolean;
};

export function fromText(text: string): EditorBuffer {
  return {
    lines: text.split("\n"),
    row: 0,
    col: 0,
    top: 0,
    dirty: false,
  };
}

export function toText(buffer: EditorBuffer): string {
  return buffer.lines.join("\n");
}

const clamp = (value: number, lo: number, hi: number): number =>
  Math.max(lo, Math.min(hi, value));

function normalized(buffer: EditorBuffer, viewRows: number): EditorBuffer {
  const row = clamp(buffer.row, 0, buffer.lines.length - 1);
  const col = clamp(buffer.col, 0, (buffer.lines[row] ?? "").length);
  let top = clamp(buffer.top, 0, Math.max(0, buffer.lines.length - viewRows));
  if (row < top) top = row;
  if (row >= top + viewRows) top = row - viewRows + 1;
  return { ...buffer, row, col, top };
}

function insertText(buffer: EditorBuffer, text: string): EditorBuffer {
  const parts = text.replace(/\r\n?/g, "\n").split("\n");
  const line = buffer.lines[buffer.row] ?? "";
  const before = line.slice(0, buffer.col);
  const after = line.slice(buffer.col);
  const lines = [...buffer.lines];
  if (parts.length === 1) {
    lines[buffer.row] = before + parts[0] + after;
    return {
      ...buffer,
      lines,
      col: buffer.col + parts[0]!.length,
      dirty: true,
    };
  }
  const inserted = [
    before + parts[0],
    ...parts.slice(1, -1),
    parts[parts.length - 1] + after,
  ];
  lines.splice(buffer.row, 1, ...inserted);
  return {
    ...buffer,
    lines,
    row: buffer.row + parts.length - 1,
    col: parts[parts.length - 1]!.length,
    dirty: true,
  };
}

/**
 * One keystroke → next buffer. Pure; the caller renders `lines` sliced
 * at `top` and paints the cursor at (row, col).
 */
export function applyEdit(
  buffer: EditorBuffer,
  input: string,
  key: EditorKey,
  viewRows: number,
): EditorBuffer {
  const line = buffer.lines[buffer.row] ?? "";

  if (key.upArrow) {
    return normalized({ ...buffer, row: buffer.row - 1 }, viewRows);
  }
  if (key.downArrow) {
    return normalized({ ...buffer, row: buffer.row + 1 }, viewRows);
  }
  if (key.leftArrow) {
    if (buffer.col > 0) {
      return normalized({ ...buffer, col: buffer.col - 1 }, viewRows);
    }
    if (buffer.row > 0) {
      const prev = buffer.lines[buffer.row - 1] ?? "";
      return normalized(
        { ...buffer, row: buffer.row - 1, col: prev.length },
        viewRows,
      );
    }
    return buffer;
  }
  if (key.rightArrow) {
    if (buffer.col < line.length) {
      return normalized({ ...buffer, col: buffer.col + 1 }, viewRows);
    }
    if (buffer.row < buffer.lines.length - 1) {
      return normalized({ ...buffer, row: buffer.row + 1, col: 0 }, viewRows);
    }
    return buffer;
  }
  if (key.pageUp) {
    return normalized(
      { ...buffer, row: buffer.row - (viewRows - 1) },
      viewRows,
    );
  }
  if (key.pageDown) {
    return normalized(
      { ...buffer, row: buffer.row + (viewRows - 1) },
      viewRows,
    );
  }
  if (key.return) {
    const lines = [...buffer.lines];
    lines.splice(
      buffer.row,
      1,
      line.slice(0, buffer.col),
      line.slice(buffer.col),
    );
    return normalized(
      { ...buffer, lines, row: buffer.row + 1, col: 0, dirty: true },
      viewRows,
    );
  }
  if (key.backspace) {
    if (buffer.col > 0) {
      const lines = [...buffer.lines];
      lines[buffer.row] =
        line.slice(0, buffer.col - 1) + line.slice(buffer.col);
      return normalized(
        { ...buffer, lines, col: buffer.col - 1, dirty: true },
        viewRows,
      );
    }
    if (buffer.row > 0) {
      const prev = buffer.lines[buffer.row - 1] ?? "";
      const lines = [...buffer.lines];
      lines.splice(buffer.row - 1, 2, prev + line);
      return normalized(
        {
          ...buffer,
          lines,
          row: buffer.row - 1,
          col: prev.length,
          dirty: true,
        },
        viewRows,
      );
    }
    return buffer;
  }
  if (key.delete) {
    if (buffer.col < line.length) {
      const lines = [...buffer.lines];
      lines[buffer.row] =
        line.slice(0, buffer.col) + line.slice(buffer.col + 1);
      return normalized({ ...buffer, lines, dirty: true }, viewRows);
    }
    if (buffer.row < buffer.lines.length - 1) {
      const next = buffer.lines[buffer.row + 1] ?? "";
      const lines = [...buffer.lines];
      lines.splice(buffer.row, 2, line + next);
      return normalized({ ...buffer, lines, dirty: true }, viewRows);
    }
    return buffer;
  }
  if (key.tab) {
    return normalized(insertText(buffer, "  "), viewRows);
  }
  if (input.length > 0 && !key.ctrl && !key.meta) {
    return normalized(insertText(buffer, input), viewRows);
  }
  return buffer;
}
