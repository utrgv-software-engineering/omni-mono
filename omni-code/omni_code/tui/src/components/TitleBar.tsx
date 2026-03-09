import React from "react";
import { Box, Text } from "ink";

type Props = {
  breadcrumbs: string[];
  right?: string;
};

export function TitleBar({ breadcrumbs, right }: Props) {
  return (
    <Box>
      <Text color="cyan" bold>
        lazyomni
      </Text>
      {breadcrumbs.map((crumb, i) => (
        <React.Fragment key={i}>
          <Text color="gray"> &gt; </Text>
          <Text color={i === breadcrumbs.length - 1 ? "white" : "gray"} bold={i === breadcrumbs.length - 1}>
            {crumb}
          </Text>
        </React.Fragment>
      ))}
      {right ? (
        <Box flexGrow={1} justifyContent="flex-end">
          <Text color="gray">{right}</Text>
        </Box>
      ) : null}
    </Box>
  );
}
