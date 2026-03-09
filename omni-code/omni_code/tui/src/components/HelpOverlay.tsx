import React from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";
import { useTheme } from "../ThemeContext.js";

export type Hint = { key: string; label: string };

type Props = {
  title: string;
  hints: Hint[];
  onClose: () => void;
};

export function HelpOverlay({ title, hints, onClose }: Props) {
  const t = useTheme();
  const { stdout } = useStdout();
  const cols = stdout?.columns ?? 80;
  const maxKeyLen = Math.max(...hints.map((h) => h.key.length));

  useInput((_input: string, key: Key) => {
    if (key.escape || _input === "?") {
      onClose();
    }
  });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={t.accent}
      paddingX={2}
      paddingY={1}
      width={Math.min(cols - 4, 60)}
    >
      <Box marginBottom={1}>
        <Text bold color={t.accent}>
          {title} — Keybindings
        </Text>
      </Box>

      {hints.map((h, i) => (
        <Box key={i}>
          <Text color={t.warning} bold>
            {h.key.padEnd(maxKeyLen + 2)}
          </Text>
          <Text color={t.fg}>{h.label}</Text>
        </Box>
      ))}

      <Box marginTop={1}>
        <Text color={t.fgSubtle}>Press ? or esc to close</Text>
      </Box>
    </Box>
  );
}
