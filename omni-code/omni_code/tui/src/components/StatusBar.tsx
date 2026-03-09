import React from "react";
import { Box, Text } from "ink";
import type { Hint } from "./KeyHint.js";

export type InputMode = "NORMAL" | "INSERT";

const MODE_COLORS: Record<InputMode, string> = {
  NORMAL: "blue",
  INSERT: "green",
};

type Props = {
  hints: Hint[];
  status?: string | null;
  mode?: InputMode;
};

export function StatusBar({ hints, status, mode }: Props) {
  return (
    <Box flexDirection="column">
      <Box flexWrap="wrap">
        {mode ? (
          <>
            <Text color="black" backgroundColor={MODE_COLORS[mode]} bold>
              {" "}{mode}{" "}
            </Text>
            <Text> </Text>
          </>
        ) : null}
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
      <Box height={1}>
        {status ? <Text color="yellow">{status}</Text> : <Text> </Text>}
      </Box>
    </Box>
  );
}
