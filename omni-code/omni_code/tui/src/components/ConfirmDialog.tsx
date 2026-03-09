import React from "react";
import { Box, Text, useInput, type Key } from "ink";
import { useTheme } from "../ThemeContext.js";

type Props = {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({ message, onConfirm, onCancel }: Props) {
  const t = useTheme();

  useInput((input: string, key: Key) => {
    if (input === "y" || input === "Y") {
      onConfirm();
      return;
    }
    if (input === "n" || input === "N" || key.escape) {
      onCancel();
      return;
    }
  });

  return (
    <Box
      borderStyle="round"
      borderColor={t.warning}
      paddingX={2}
      paddingY={0}
      flexDirection="column"
    >
      <Text color={t.warning}>{message}</Text>
      <Text>
        <Text color={t.success} bold>y</Text>
        <Text color={t.fgSubtle}>es / </Text>
        <Text color={t.error} bold>n</Text>
        <Text color={t.fgSubtle}>o</Text>
      </Text>
    </Box>
  );
}
