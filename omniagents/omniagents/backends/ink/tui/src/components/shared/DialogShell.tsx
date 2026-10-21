/**
 * Bordered container shared by approval dialogs. Provides a title row, a
 * gap-1 column body for caller-supplied content, and an optional footer row
 * for hint text. Centralises the Tokyo Night border/title styling so dialogs
 * don't drift apart visually.
 */

import React from "react";
import { Box, Text } from "ink";
import { Theme } from "../../theme.js";

export interface DialogShellProps {
  title: string;
  footer?: string;
  children: React.ReactNode;
}

export const DialogShell: React.FC<DialogShellProps> = ({
  title,
  footer,
  children,
}) => {
  return (
    <Box
      flexDirection="column"
      borderStyle="single"
      borderColor={Theme.borderAmbient}
      paddingX={2}
      gap={1}
    >
      <Box>
        <Text bold color={Theme.stateBlocked}>
          {title}
        </Text>
      </Box>
      {children}
      {footer && (
        <Box>
          <Text color={Theme.textChrome}>{footer}</Text>
        </Box>
      )}
    </Box>
  );
};
