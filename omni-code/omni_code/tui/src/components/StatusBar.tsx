import React from "react";
import { Box, Text } from "ink";
import type { Hint } from "./KeyHint.js";
import { useTheme } from "../ThemeContext.js";

export type InputMode = "NORMAL" | "INSERT";

type Props = {
  hints: Hint[];
  status?: string | null;
  mode?: InputMode;
};

export function StatusBar({ hints, status, mode }: Props) {
  const t = useTheme();
  const modeColors: Record<InputMode, string> = {
    NORMAL: t.modeNormal,
    INSERT: t.modeInsert,
  };

  return (
    <Box flexDirection="column">
      <Box flexWrap="wrap">
        {mode ? (
          <>
            <Text color="black" backgroundColor={modeColors[mode]} bold>
              {" "}{mode}{" "}
            </Text>
            <Text> </Text>
          </>
        ) : null}
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
      <Box height={1}>
        {status ? <Text color={t.warning}>{status}</Text> : <Text> </Text>}
      </Box>
    </Box>
  );
}
