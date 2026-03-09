import React from "react";
import { Box, Text } from "ink";
import { useTheme } from "../ThemeContext.js";

type Props = {
  breadcrumbs: string[];
  right?: string;
};

export function TitleBar({ breadcrumbs, right }: Props) {
  const t = useTheme();
  return (
    <Box>
      <Text color={t.accent} bold>
        lazyomni
      </Text>
      {breadcrumbs.map((crumb, i) => (
        <React.Fragment key={i}>
          <Text color={t.fgSubtle}> &gt; </Text>
          <Text color={i === breadcrumbs.length - 1 ? t.fg : t.fgSubtle} bold={i === breadcrumbs.length - 1}>
            {crumb}
          </Text>
        </React.Fragment>
      ))}
      {right ? (
        <Box flexGrow={1} justifyContent="flex-end">
          <Text color={t.fgSubtle}>{right}</Text>
        </Box>
      ) : null}
    </Box>
  );
}
