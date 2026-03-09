import React from "react";
import { Box, Text, useInput, type Key } from "ink";

type Props = {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({ message, onConfirm, onCancel }: Props) {
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
      borderColor="yellow"
      paddingX={2}
      paddingY={0}
      flexDirection="column"
    >
      <Text color="yellow">{message}</Text>
      <Text>
        <Text color="green" bold>y</Text>
        <Text color="gray">es / </Text>
        <Text color="red" bold>n</Text>
        <Text color="gray">o</Text>
      </Text>
    </Box>
  );
}
