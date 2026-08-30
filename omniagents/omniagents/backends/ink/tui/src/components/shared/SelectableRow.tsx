import React from "react";
import { Box } from "ink";
import { Theme } from "../../theme.js";

/**
 * The one way a list marks its selected row: a full-width highlight
 * band, the way every terminal UI does it — vim's `cursorline`, and the
 * pickers built on it.
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
export const SelectableRow = React.memo<{
  selected: boolean;
  children: React.ReactNode;
}>(({ selected, children }) => (
  <Box backgroundColor={selected ? Theme.selectionBg : undefined}>
    {children}
  </Box>
));

SelectableRow.displayName = "SelectableRow";
