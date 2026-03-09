/**
 * TerminalView — renders pre-computed styled lines from a PtySession.
 * Shared between TileTree and TicketTileTree.
 */

import React from "react";
import { Box, Text } from "ink";
import type { RenderedLine } from "./PtySession.js";

export function TerminalView({ lines, cols, rows }: { lines: RenderedLine[]; cols: number; rows: number }) {
  return (
    <Box flexDirection="column" width={cols} height={rows}>
      {lines.map((spans, y) => (
        <Box key={y}>
          {spans.map((span, i) => (
            <Text
              key={i}
              color={span.style.fg}
              backgroundColor={span.style.bg}
              bold={span.style.bold}
              dimColor={span.style.dim}
              italic={span.style.italic}
              underline={span.style.underline}
              strikethrough={span.style.strikethrough}
              inverse={span.style.inverse}
            >
              {span.text}
            </Text>
          ))}
        </Box>
      ))}
    </Box>
  );
}
