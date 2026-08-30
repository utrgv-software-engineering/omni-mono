import React from "react";
import { Box, Text } from "ink";
import { Theme } from "../../theme.js";

/**
 * The one way a list marks its selected row.
 *
 * Every sidebar, drawer and picker in the TUI renders rows through this
 * component so selection looks identical everywhere. It had drifted:
 * fourteen hand-rolled cursors across thirteen files, half of them
 * `"▶ "` (two columns) and the rest `"▶"` (one), coloured three
 * different ways. `sidebarPattern.test.ts` now fails if a fifteenth
 * appears.
 *
 * TWO signals, on purpose:
 *
 * - a muted background band, which is what reads at a glance; and
 * - the cursor glyph plus bold, which is what survives.
 *
 * The glyph is not redundant decoration. Chalk strips backgrounds AND
 * bold under `NO_COLOR` or a monochrome terminal, so a highlight-only
 * row would leave nothing at all to say which row is selected. The band
 * carries it when there is colour; the glyph guarantees it when there
 * is not.
 *
 * Selection and focus are different questions and get different
 * answers. A sidebar can hold a selection while the keyboard belongs to
 * another pane — Review and Files both do this, changing their border
 * colour to say so. An unfocused selection keeps its glyph, dimmed, and
 * drops the band: the band means "this is the row your keys act on".
 */
export const SelectableRow = React.memo<{
  selected: boolean;
  /**
   * Whether this list currently owns the keyboard. Default true for the
   * many lists that are only rendered while focused; pass the pane's
   * real focus state for a sidebar that can be selected-but-inert.
   */
  focused?: boolean;
  children: React.ReactNode;
}>(({ selected, focused = true, children }) => {
  const active = selected && focused;
  return (
    <Box backgroundColor={active ? Theme.selectionBg : undefined}>
      <Text
        // Dim rather than absent when unfocused: the row is still the
        // one a returning keypress will act on, so it must stay findable
        // without claiming the keyboard it does not have.
        color={active ? Theme.focus : Theme.textChrome}
        bold={active}
      >
        {selected ? "▶ " : "  "}
      </Text>
      {children}
    </Box>
  );
});

SelectableRow.displayName = "SelectableRow";
