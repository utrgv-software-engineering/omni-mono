import React from "react";
import { Box, Text, useInput, useStdout, type Key } from "ink";

export type Hint = { key: string; label: string };

type Props = {
  title: string;
  hints: Hint[];
  onClose: () => void;
};

export function HelpOverlay({ title, hints, onClose }: Props) {
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
      borderColor="cyan"
      paddingX={2}
      paddingY={1}
      width={Math.min(cols - 4, 60)}
    >
      <Box marginBottom={1}>
        <Text bold color="cyan">
          {title} — Keybindings
        </Text>
      </Box>

      {hints.map((h, i) => (
        <Box key={i}>
          <Text color="yellow" bold>
            {h.key.padEnd(maxKeyLen + 2)}
          </Text>
          <Text>{h.label}</Text>
        </Box>
      ))}

      <Box marginTop={1}>
        <Text color="gray">Press ? or esc to close</Text>
      </Box>
    </Box>
  );
}
