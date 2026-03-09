import React from "react";
import { Box, Text } from "ink";
import { useTheme } from "../ThemeContext.js";

export type Hint = { key: string; label: string };

export function KeyBar({ hints }: { hints: Hint[] }) {
  const t = useTheme();
  return (
    <Box flexWrap="wrap">
      {hints.map((h, i) => (
        <React.Fragment key={i}>
          {i > 0 ? <Text> </Text> : null}
          <Text color={t.accent} bold>
            {h.key}
          </Text>
          <Text color={t.fgSubtle}>
            {" "}
            {h.label}
          </Text>
        </React.Fragment>
      ))}
    </Box>
  );
}
