/**
 * Help text component showing keyboard shortcuts and usage info
 */

import React from "react";
import { Box, Text } from "ink";
import { TokyoNightTheme } from "../theme.js";
import type { UsageInfo } from "../store/appStore.js";

// Helper function to format numbers with commas
function formatNumber(n: number): string {
  return n.toLocaleString();
}

// Helper function to format usage info
function formatUsage(usage: UsageInfo, modelOverride?: string | null): string {
  const modelName = modelOverride || usage.model || "unknown";
  if (usage.maxInputTokens && usage.maxInputTokens > 0) {
    const percentage = Math.round(
      (usage.totalTokens / usage.maxInputTokens) * 100,
    );
    return `context window: ${percentage}% • ${modelName}`;
  }
  return `tokens: ${formatNumber(usage.totalTokens)} • ${modelName}`;
}

export interface HelpProps {
  isThinking?: boolean;
  isPendingApproval?: boolean;
  connected?: boolean;
  usage?: UsageInfo | null;
  sessionModel?: string | null;
  waitingForSecondCtrlC?: boolean;
}

export const Help = React.memo<HelpProps>(
  ({
    isThinking = false,
    isPendingApproval = false,
    connected = true,
    usage = null,
    sessionModel = null,
    waitingForSecondCtrlC = false,
  }) => {
    let helpText = "";

    // Special warning when waiting for second Ctrl+C
    if (waitingForSecondCtrlC) {
      return (
        <Box justifyContent="space-between" width="100%">
          <Box>
            <Text bold color={TokyoNightTheme.warning}>
              Press Ctrl+C again to quit
            </Text>
            <Text color={TokyoNightTheme.help}> • or wait to continue</Text>
          </Box>
          {usage && (
            <Text color={TokyoNightTheme.help}>
              {formatUsage(usage, sessionModel)}
            </Text>
          )}
        </Box>
      );
    }

    if (isPendingApproval) {
      helpText = "↑/↓ navigate • enter select • esc deny";
    } else if (isThinking) {
      helpText = "esc cancel • ctrl+c quit";
    } else if (connected) {
      helpText =
        "enter send • alt+enter new line • shift+↑/↓ scroll • ctrl+c quit";
    } else {
      helpText = "ctrl+c quit";
    }

    return (
      <Box justifyContent="space-between" width="100%">
        <Text color={TokyoNightTheme.help}>{helpText}</Text>
        {usage && (
          <Text color={TokyoNightTheme.help}>
            {formatUsage(usage, sessionModel)}
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) => {
    // Custom comparison to prevent re-renders when usage object reference changes but values are the same
    if (
      prev.isThinking !== next.isThinking ||
      prev.isPendingApproval !== next.isPendingApproval ||
      prev.connected !== next.connected ||
      prev.sessionModel !== next.sessionModel ||
      prev.waitingForSecondCtrlC !== next.waitingForSecondCtrlC
    ) {
      return false;
    }

    // Deep compare usage
    if (prev.usage === next.usage) return true;
    if (!prev.usage || !next.usage) return prev.usage === next.usage;

    return (
      prev.usage.totalTokens === next.usage.totalTokens &&
      prev.usage.model === next.usage.model &&
      prev.usage.maxInputTokens === next.usage.maxInputTokens
    );
  },
);
