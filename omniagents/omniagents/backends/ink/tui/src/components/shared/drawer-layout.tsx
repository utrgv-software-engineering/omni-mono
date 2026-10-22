import React from "react";
import { Box } from "ink";
import { useTerminalSize } from "../../hooks/useTerminalSize.js";

/**
 * Layout rules every footer drawer shares.
 *
 * Drawers are fixed chrome between the transcript and the status bar,
 * like VS Code's bottom panel. Nothing clips them, so two things are on
 * them to guarantee: a row is one terminal line, and the whole drawer
 * leaves the transcript room. `/skills` broke both with a long
 * description and a body pane sized to the terminal minus a guess; the
 * helpers here are what fixed it, shared so no drawer re-derives them.
 */

/** Rows a drawer's border, header, and hint line take. */
const DRAWER_CHROME = 4;
const MIN_BUDGET = 4;

/** Body rows a drawer may use: at most half the terminal, minus its chrome. */
export function drawerBudget(terminalRows: number): number {
  return Math.max(MIN_BUDGET, Math.floor(terminalRows / 2) - DRAWER_CHROME);
}

/** `drawerBudget` for the live terminal; 24 rows when there is none (tests). */
export function useDrawerBudget(): number {
  return drawerBudget(useTerminalSize().rows);
}

/** Text as one line: newlines and runs of whitespace collapse to a space. */
export function oneLine(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

type BoxProps = React.ComponentProps<typeof Box>;

/**
 * A row segment that keeps its width: glyphs, ids, counts, trailing
 * notes. Put the `<Text>` inside with `wrap="truncate-end"` when it
 * could still be long (a name column with a `width`).
 */
export const Fixed = ({ children, ...rest }: BoxProps) => (
  <Box flexShrink={0} {...rest}>
    {children}
  </Box>
);

/**
 * The row segment that gives way: it shrinks to whatever the fixed
 * segments leave and its `<Text wrap="truncate-end">` is cut there, so
 * the row stays one line no matter how long the text is.
 */
export const Clip = ({ children, ...rest }: BoxProps) => (
  <Box flexShrink={1} minWidth={0} {...rest}>
    {children}
  </Box>
);
