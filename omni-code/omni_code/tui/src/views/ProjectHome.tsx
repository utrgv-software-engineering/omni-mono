import React from "react";
import { Box, Text } from "ink";
import { Panel } from "../components/Panel.js";
import { TileTree } from "../tiling/TileTree.js";
import type { PtySession } from "../tiling/PtySession.js";
import type { TileNode } from "../tiling/tree.js";
import { useTheme } from "../ThemeContext.js";

// --- Split picker for Ctrl+N overlay ---

function SplitPickerOverlay() {
  const t = useTheme();
  return (
    <Panel title="New Split" focused={true} focusedColor={t.success} flexGrow={1}>
      <Box flexDirection="column" paddingX={1} paddingY={1}>
        <Box marginBottom={1}>
          <Text color={t.fg} bold>Split with:</Text>
        </Box>
        <Box><Text color={t.fg}>  <Text color={t.accent} bold>c</Text> Chat</Text></Box>
        <Box><Text color={t.fg}>  <Text color={t.accent} bold>t</Text> Terminal</Text></Box>
        <Box marginTop={1}>
          <Text color={t.fgSubtle}>Press a key to split, or Esc to cancel.</Text>
        </Box>
      </Box>
    </Panel>
  );
}

// --- Component (pure renderer) ---

type Props = {
  tilingRoot: TileNode | null;
  focusedTileId: string | null;
  /** Whether the tile area is the currently active region. */
  active: boolean;
  insertMode: boolean;
  sessions: Map<string, PtySession>;
  pickerOpen: boolean;
  availCols: number;
  availRows: number;
};

export function ProjectHome({
  tilingRoot,
  focusedTileId,
  active,
  insertMode,
  sessions,
  pickerOpen,
  availCols,
  availRows,
}: Props) {
  if (!tilingRoot) {
    return null; // Picker tile will be auto-inserted
  }

  if (pickerOpen) {
    return (
      <Box flexGrow={1} flexDirection="row">
        <Box flexGrow={1}>
          <TileTree
            node={tilingRoot}
            availCols={Math.max(10, availCols - 30)}
            availRows={availRows}
            focusedId={focusedTileId}
            active={active}
            rawFocused={insertMode}
            sessions={sessions}
          />
        </Box>
        <Box width={28} flexShrink={0}>
          <SplitPickerOverlay />
        </Box>
      </Box>
    );
  }

  return (
    <Box flexGrow={1}>
      <TileTree
        node={tilingRoot}
        availCols={availCols}
        availRows={availRows}
        focusedId={focusedTileId}
        active={active}
        rawFocused={insertMode}
        sessions={sessions}
      />
    </Box>
  );
}
