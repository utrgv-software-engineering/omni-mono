import React from "react";
import { Box, Text, useInput, type Key } from "ink";

type Props = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  onCancel: () => void;
  placeholder?: string;
  prompt?: string;
};

export function TextInput({ value, onChange, onSubmit, onCancel, placeholder, prompt }: Props) {
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
      {prompt ? <Text color="cyan">{prompt} </Text> : null}
      <Text>{value || (placeholder ? <Text color="gray">{placeholder}</Text> : "")}</Text>
      <Text color="cyan">_</Text>
    </Box>
  );
}
