import React from "react";
import { Box, Text } from "ink";
import { Panel } from "../components/Panel.js";
import { TileTree } from "../tiling/TileTree.js";
import type { PtySession } from "../tiling/PtySession.js";
import type { TileNode } from "../tiling/tree.js";
import { useTheme } from "../ThemeContext.js";

// --- Split picker options ---

type SplitOption = {
  id: string;
  key: string;
  label: string;
};

const SPLIT_OPTIONS: SplitOption[] = [
  { id: "chat", key: "c", label: "Chat" },
  { id: "terminal", key: "t", label: "Terminal" },
];

// --- SplitPicker component ---

function SplitPicker({ isEmpty }: { isEmpty: boolean }) {
  const t = useTheme();
  return (
    <Panel title="New Split" focused={true} focusedColor={t.success} flexGrow={1}>
      <Box flexDirection="column" paddingX={1} paddingY={1}>
        <Box marginBottom={1}>
          <Text color={t.fg} bold>{isEmpty ? "Welcome — choose what to open:" : "Split with:"}</Text>
        </Box>
        {SPLIT_OPTIONS.map((opt) => (
          <Box key={opt.id}>
            <Text color={t.fg}>  <Text color={t.accent} bold>{opt.key}</Text> {opt.label}</Text>
          </Box>
        ))}
        <Box marginTop={1}>
          <Text color={t.fgSubtle}>
            {isEmpty
              ? "Press a key to open, or Esc to go back."
              : "Press a key to split, or Esc to cancel."}
          </Text>
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
  const showPicker = pickerOpen || !tilingRoot;

  if (showPicker && !tilingRoot) {
    return <SplitPicker isEmpty={true} />;
  }

  if (showPicker && tilingRoot) {
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
          <SplitPicker isEmpty={false} />
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
