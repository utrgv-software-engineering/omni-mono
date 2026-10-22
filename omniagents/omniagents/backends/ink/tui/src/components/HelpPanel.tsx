import React from "react";
import { Box, Text } from "ink";
import { useTerminalSize } from "../hooks/useTerminalSize.js";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { helpSections, type HelpSection } from "./help-model.js";
import type { Screen } from "./StatusBar.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

type Props = {
  open: boolean;
  screen: Screen;
  onClose: () => void;
};

/** Rows a section occupies: its title plus one per binding plus a gap. */
const sectionRows = (section: HelpSection) => section.rows.length + 2;

/**
 * Split sections across two columns so the overlay fits a short
 * terminal. Greedy by row count: each section goes to the shorter
 * column, order preserved within a column.
 */
export function splitColumns(
  sections: HelpSection[],
): [HelpSection[], HelpSection[]] {
  const left: HelpSection[] = [];
  const right: HelpSection[] = [];
  let leftRows = 0;
  let rightRows = 0;
  for (const section of sections) {
    if (leftRows <= rightRows) {
      left.push(section);
      leftRows += sectionRows(section);
    } else {
      right.push(section);
      rightRows += sectionRows(section);
    }
  }
  return [left, right];
}

/**
 * The `?` overlay: every key, in one place. Modal while open — any key
 * that is not a chord for the global layer closes it, so it never
 * swallows a keystroke the user meant for the composer. Two columns and
 * a hard height cap keep it inside the terminal; a frame taller than
 * the screen is what corrupts Ink's redraw.
 */
export const HelpPanel = themedMemo<Props>(({ open, screen, onClose }) => {
  const { rows } = useTerminalSize();
  useKeypress(
    (_input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab still cycles screens
      onClose();
      return true;
    },
    { isActive: open, priority: KeypressPriority.High },
  );

  if (!open) return null;
  const sections = helpSections(screen);
  const keyWidth =
    Math.max(
      ...sections.flatMap((section) =>
        section.rows.map((row) => row.keys.length),
      ),
    ) + 2;
  const [left, right] = splitColumns(sections);
  const column = (items: HelpSection[]) => (
    <Box flexDirection="column" flexBasis={0} flexGrow={1} minWidth={0}>
      {items.map((section) => (
        <Box key={section.title} flexDirection="column" marginTop={1}>
          <Text color={Theme.textSecondary} bold>
            {section.title}
          </Text>
          {section.rows.map((row) => (
            <Box key={row.keys}>
              <Box width={keyWidth} flexShrink={0}>
                <Text color={Theme.accentUser} wrap="truncate-end">
                  {row.keys}
                </Text>
              </Box>
              <Box flexShrink={1} minWidth={0}>
                <Text color={Theme.textChrome} wrap="truncate-end">
                  {row.label}
                </Text>
              </Box>
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  );
  // Title row + tallest column + border rows, never past the terminal.
  const tallest = Math.max(
    left.reduce((n, s) => n + sectionRows(s), 0),
    right.reduce((n, s) => n + sectionRows(s), 0),
  );
  const height = Math.min(tallest + 3, Math.max(8, rows - 9));

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      marginRight={1}
      height={height}
      overflowY="hidden"
      borderStyle="single"
      borderColor={Theme.borderAmbient}
    >
      <Box justifyContent="space-between">
        <Text color={Theme.textPrimary} bold>
          Keys
        </Text>
        <Text color={Theme.textChrome}>
          any key closes · /help lists commands
        </Text>
      </Box>
      <Box gap={2}>
        {column(left)}
        {column(right)}
      </Box>
    </Box>
  );
});
