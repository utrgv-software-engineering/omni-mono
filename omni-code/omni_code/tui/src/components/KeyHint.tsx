import React from "react";
import { Box, Text } from "ink";

export type Hint = { key: string; label: string };

export function KeyBar({ hints }: { hints: Hint[] }) {
  return (
    <Box flexWrap="wrap">
      {hints.map((h, i) => (
        <React.Fragment key={i}>
          {i > 0 ? <Text> </Text> : null}
          <Text color="cyan" bold>
            {h.key}
          </Text>
          <Text color="gray">
            {" "}
            {h.label}
          </Text>
        </React.Fragment>
      ))}
    </Box>
  );
}
