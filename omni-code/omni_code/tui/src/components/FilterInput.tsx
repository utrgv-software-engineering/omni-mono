import React from "react";
import { Box, Text, useInput, type Key } from "ink";
import { useTheme } from "../ThemeContext.js";

type Props = {
  value: string;
  onChange: (value: string) => void;
  onClose: () => void;
};

export function FilterInput({ value, onChange, onClose }: Props) {
  const t = useTheme();

  useInput((input: string, key: Key) => {
    if (key.escape) {
      onClose();
      return;
    }
    if (key.backspace || key.delete) {
      onChange(value.slice(0, -1));
      return;
    }
    if (!key.ctrl && !key.meta && !key.return && input) {
      onChange(value + input);
    }
  });

  return (
    <Box>
      <Text color={t.accent}>/</Text>
      <Text color={t.fg}>{value}</Text>
      <Text color={t.accent}>_</Text>
    </Box>
  );
}

export function fuzzyMatch(text: string, query: string): boolean {
  if (!query) return true;
  return text.toLowerCase().includes(query.toLowerCase());
}
