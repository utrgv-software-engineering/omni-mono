import React from "react";
import { Box, Text, useInput, type Key } from "ink";
import { useTheme } from "../ThemeContext.js";

type Props = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  onCancel: () => void;
  placeholder?: string;
  prompt?: string;
};

export function TextInput({ value, onChange, onSubmit, onCancel, placeholder, prompt }: Props) {
  const t = useTheme();

  useInput((input: string, key: Key) => {
    if (key.escape) {
      onCancel();
      return;
    }
    if (key.return) {
      onSubmit(value);
      return;
    }
    if (key.backspace || key.delete) {
      onChange(value.slice(0, -1));
      return;
    }
    if (!key.ctrl && !key.meta && input) {
      onChange(value + input);
    }
  });

  return (
    <Box>
      {prompt ? <Text color={t.accent}>{prompt} </Text> : null}
      <Text color={t.fg}>{value || (placeholder ? <Text color={t.fgSubtle}>{placeholder}</Text> : "")}</Text>
      <Text color={t.accent}>_</Text>
    </Box>
  );
}
