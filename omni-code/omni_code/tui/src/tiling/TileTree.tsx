/**
 * TileTree — recursive React component that renders the tiling layout.
 *
 * Split nodes become nested Ink Boxes with flexDirection.
 * Leaf nodes render a Panel containing a TerminalView.
 * PTY sessions are managed externally (PtySession map in ProjectHome).
 */

import React from "react";
import { Box, Text } from "ink";
import { Panel } from "../components/Panel.js";
import type { TileNode } from "./tree.js";
import type { PtySession } from "./PtySession.js";
import { TerminalView } from "./TerminalView.js";
import { useTheme } from "../ThemeContext.js";

// --- TileTree ---

type Props = {
  node: TileNode;
  availCols: number;
  availRows: number;
  focusedId: string | null;
  /** Whether the tile area is the currently active region (user's focus is on a tile). */
  active?: boolean;
  rawFocused?: boolean;
  sessions: Map<string, PtySession>;
};

export function TileTree({ node, availCols, availRows, focusedId, active = false, rawFocused = false, sessions }: Props) {
  const t = useTheme();
  if (node.type === "leaf") {
    const isFocused = node.id === focusedId;
    const isRawFocused = isFocused && rawFocused;
    // Only show as focused if the tile area is the active region
    const showFocused = isFocused && active;

    // Picker tile — welcome / split chooser
    if (node.tileType === "picker") {
      return (
        <Panel title="New Split" focused={showFocused} focusedColor={t.success} flexGrow={1}>
          <Box flexDirection="column" paddingX={1} paddingY={1}>
            <Box marginBottom={1}>
              <Text color={t.fg} bold>Welcome — choose what to open:</Text>
            </Box>
            <Box><Text color={t.fg}>  <Text color={t.accent} bold>c</Text> Chat</Text></Box>
            <Box><Text color={t.fg}>  <Text color={t.accent} bold>t</Text> Terminal</Text></Box>
            <Box marginTop={1}>
              <Text color={t.fgSubtle}>Press a key to open, or Esc to go back.</Text>
            </Box>
          </Box>
        </Panel>
      );
    }

    const session = sessions.get(node.id);
    // Panel borders consume 2 cols and 2 rows, title line consumes 1 row
    const innerCols = Math.max(10, availCols - 2);
    const innerRows = Math.max(3, availRows - 3);

    // Ensure PTY is sized correctly
    if (session && (session as any)._dead !== true) {
      session.resize(innerCols, innerRows);
    }

    const lines = session ? session.readBuffer(innerCols, innerRows) : [];
    const label = node.tileType === "chat" ? "Chat" : "Term";

    return (
      <Panel title={`${label} [${node.id}]`} focused={showFocused} rawFocused={isRawFocused} flexGrow={1}>
        {lines.length > 0 ? (
          <TerminalView lines={lines} cols={innerCols} rows={innerRows} />
        ) : (
          <Text color={t.fgSubtle}> starting...</Text>
        )}
      </Panel>
    );
  }

  // Split node
  const isH = node.direction === "horizontal";
  const aSize = isH
    ? Math.floor(availCols * node.ratio)
    : Math.floor(availRows * node.ratio);
  const bSize = isH ? availCols - aSize : availRows - aSize;

  return (
    <Box flexDirection={isH ? "row" : "column"} flexGrow={1}>
      <Box
        width={isH ? aSize : undefined}
        height={!isH ? aSize : undefined}
        flexGrow={0}
        flexShrink={0}
        flexDirection="column"
      >
        <TileTree
          node={node.a}
          availCols={isH ? aSize : availCols}
          availRows={isH ? availRows : aSize}
          focusedId={focusedId}
          active={active}
          rawFocused={rawFocused}
          sessions={sessions}
        />
      </Box>
      <Box
        width={isH ? bSize : undefined}
        height={!isH ? bSize : undefined}
        flexGrow={0}
        flexShrink={0}
        flexDirection="column"
      >
        <TileTree
          node={node.b}
          availCols={isH ? bSize : availCols}
          availRows={isH ? availRows : bSize}
          focusedId={focusedId}
          active={active}
          rawFocused={rawFocused}
          sessions={sessions}
        />
      </Box>
    </Box>
  );
}
