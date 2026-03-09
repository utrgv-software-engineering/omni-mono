import React, { type ReactNode } from "react";
import { Box, Text, useStdout } from "ink";
import { useTheme } from "../ThemeContext.js";

type Props = {
  title?: string;
  focused?: boolean;
  focusedColor?: string;
  rawFocused?: boolean;
  height?: number | string;
  width?: number | string;
  flexGrow?: number;
  flexShrink?: number;
  children: ReactNode;
  badge?: ReactNode;
};

export function Panel({
  title,
  focused = false,
  focusedColor,
  rawFocused = false,
  height,
  width,
  flexGrow,
  flexShrink,
  children,
  badge,
}: Props) {
  const t = useTheme();
  const borderColor = rawFocused ? t.borderRaw : focused ? (focusedColor ?? t.borderFocused) : t.border;
  const borderStyle = "single";

  return (
    <Box
      flexDirection="column"
      borderStyle={borderStyle}
      borderColor={borderColor}
      height={height}
      width={width}
      flexGrow={flexGrow}
      flexShrink={flexShrink}
      overflow="hidden"
    >
      {title ? (
        <Box>
          <Text color={borderColor} bold={focused || rawFocused}>
            {title}
          </Text>
          {rawFocused ? (
            <Text color={t.fgSubtle}> esc release</Text>
          ) : null}
          {badge ? (
            <>
              <Text> </Text>
              {badge}
            </>
          ) : null}
        </Box>
      ) : null}
      {children}
    </Box>
  );
}

export function useTerminalSize() {
  const { stdout } = useStdout();
  return {
    cols: stdout?.columns ?? 80,
    rows: stdout?.rows ?? 24,
  };
}
