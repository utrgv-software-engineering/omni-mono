/**
 * Help text component showing keyboard shortcuts and usage info
 */

import React from 'react';
import { Box, Text } from 'ink';
import { TokyoNightTheme } from '../theme.js';
import type { UsageInfo } from '../hooks/useMessages.js';

export interface HelpProps {
  isThinking?: boolean;
  isPendingApproval?: boolean;
  connected?: boolean;
  usage?: UsageInfo | null;
}

export const Help = React.memo<HelpProps>(({
  isThinking = false,
  isPendingApproval = false,
  connected = true,
  usage = null,
}) => {
  let helpText = '';

  if (isPendingApproval) {
    helpText = '↑/↓ navigate • enter select • esc deny';
  } else if (isThinking) {
    helpText = 'esc cancel • ctrl+c quit';
  } else if (connected) {
    helpText = 'enter send • alt+enter new line • ctrl+c quit';
  } else {
    helpText = 'ctrl+c quit';
  }

  // Format usage info
  let usageText = '';
  if (usage) {
    const modelName = usage.model || 'unknown';
    if (usage.contextWindow && usage.contextWindow > 0) {
      // Show percentage if context window is known
      const remaining = usage.contextWindow - usage.totalTokens;
      const percentage = Math.round((remaining / usage.contextWindow) * 100);
      usageText = `context remaining: ${percentage}% • ${modelName}`;
    } else {
      // Show token counts if context window is unknown
      usageText = `tokens: ${usage.totalTokens} • ${modelName}`;
    }
  }

  return (
    <Box justifyContent="space-between" width="100%">
      <Text color={TokyoNightTheme.help}>{helpText}</Text>
      {usageText && <Text color={TokyoNightTheme.help}>{usageText}</Text>}
    </Box>
  );
}, (prev, next) => {
  // Custom comparison to prevent re-renders when usage object reference changes but values are the same
  if (prev.isThinking !== next.isThinking ||
      prev.isPendingApproval !== next.isPendingApproval ||
      prev.connected !== next.connected) {
    return false;
  }

  // Deep compare usage
  if (prev.usage === next.usage) return true;
  if (!prev.usage || !next.usage) return prev.usage === next.usage;

  return prev.usage.totalTokens === next.usage.totalTokens &&
         prev.usage.model === next.usage.model &&
         prev.usage.contextWindow === next.usage.contextWindow;
});
