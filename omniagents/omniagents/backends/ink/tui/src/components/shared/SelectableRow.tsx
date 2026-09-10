import React, { createContext, useContext } from "react";
import { Box, Text } from "ink";
import { Theme } from "../../theme.js";
import { themedMemo } from "../../theme/themed-memo.js";

/**
 * The one way a list marks its selected row: a full-width highlight
 * band plus bold text — neo-tree's `NeoTreeCursorLine`, which is
 * `CursorLine` with `bold` on top, and vim's cursorline before it.
 *
 * No cursor glyph. The band IS the marker; a leading caret on top of a
 * highlight is belt-and-braces that costs two columns on every row of
 * every list.
 *
 * Which pane owns the keyboard is a separate question with a separate
 * answer: the sidebars change their border colour. The band does not
 * need to say it too.
 *
 * Selection had drifted to fourteen hand-rolled cursors across thirteen
 * files before this existed; `sidebarPattern.test.ts` is what keeps a
 * fifteenth from appearing.
 */

/**
 * Bold cannot ride the band down from the row.
 *
 * In a terminal, vim applies a highlight group to the whole line and is
 * done. Ink has no equivalent: `bold` is a `<Text>` prop, `<Box>` does
 * not cascade it, and wrapping the row in a `<Text>` is impossible —
 * Ink throws `<Box> can't be nested inside <Text>`, and most rows hold
 * a `<Box>` for their truncating column.
 *
 * So the row publishes its selected state and `RowText` reads it. Rows
 * use `RowText` in place of `Text`; nothing else changes.
 */
const SelectedContext = createContext(false);

type TextProps = React.ComponentProps<typeof Text>;

/**
 * `Text` that bolds itself when its row is selected. An explicit
 * `bold` still wins, so a row can bold something for its own reasons
 * (an active model, a current branch) independently of selection.
 */
export const RowText = ({ bold, children, ...rest }: TextProps) => {
  const selected = useContext(SelectedContext);
  return (
    <Text bold={bold || selected} {...rest}>
      {children}
    </Text>
  );
};

export const SelectableRow = themedMemo<{
  selected: boolean;
  children: React.ReactNode;
}>(({ selected, children }) => (
  <SelectedContext.Provider value={selected}>
    <Box backgroundColor={selected ? Theme.selectionBg : undefined}>
      {children}
    </Box>
  </SelectedContext.Provider>
));

SelectableRow.displayName = "SelectableRow";
