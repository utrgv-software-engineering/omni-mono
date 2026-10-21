// The token under the composer cursor that a popup can complete.
//
// Two shapes, both detected on the text BEFORE the cursor on the cursor's
// own row so the popup follows what the user is typing, not what happens
// to sit to the right of it:
//
//   /que      — a slash command, only at the very start of the message
//   ... @src/ap — a file mention, anywhere a word can start
//
// Everything else is `null`: no popup. Kept free of Ink so it is unit
// testable and so the composer's reducer can stay ignorant of popups.

export type ComposerTokenKind = "slash" | "mention";

export type ComposerToken = {
  kind: ComposerTokenKind;
  /** Text after the sigil, up to the cursor. */
  query: string;
  /** Row the token is on. */
  row: number;
  /** Code-point column of the sigil. */
  start: number;
  /** Code-point column of the cursor (exclusive end of the token). */
  end: number;
};

export function detectComposerToken(
  lines: readonly string[],
  cursorRow: number,
  cursorCol: number,
): ComposerToken | null {
  const line = lines[cursorRow] ?? "";
  const before = Array.from(line).slice(0, cursorCol).join("");
  if (cursorRow === 0) {
    const slash = /^\/(\S*)$/.exec(before);
    if (slash) {
      return {
        kind: "slash",
        query: slash[1] ?? "",
        row: cursorRow,
        start: 0,
        end: cursorCol,
      };
    }
  }
  const mention = /(?:^|\s)@(\S*)$/.exec(before);
  if (mention) {
    const query = mention[1] ?? "";
    const start = cursorCol - Array.from(query).length - 1;
    return { kind: "mention", query, row: cursorRow, start, end: cursorCol };
  }
  return null;
}

/** What a popup hands back: replace `token` with `text`, maybe send. */
export type ComposerCompletion = {
  token: ComposerToken;
  text: string;
  /** Send the message right after inserting (a slash command with no arguments). */
  submit: boolean;
};

/** Stable identity for "the same token the user dismissed". */
export function tokenKey(token: ComposerToken): string {
  return `${token.kind}:${token.row}:${token.start}`;
}

/**
 * Esc hides the popup for the token under the cursor; it stays hidden
 * while the user keeps typing that same token, and comes back as soon as
 * they leave it — by finishing the word, deleting it, or sending. A
 * dismissal must not outlive its token: `/` at the start of a message is
 * the same position every time, and it has to open again on the next one.
 */
export function nextDismissedToken(
  dismissed: string | null,
  token: ComposerToken | null,
): string | null {
  if (dismissed === null || token === null) return null;
  return tokenKey(token) === dismissed ? dismissed : null;
}
