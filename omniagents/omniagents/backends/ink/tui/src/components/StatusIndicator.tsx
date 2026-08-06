import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useThinking, useStatus } from "../store/appStore.js";
import { Theme } from "../theme.js";

// Animated spinner with status text
export const StatusIndicator = React.memo(() => {
  const thinking = useThinking();
  const statusObj = useStatus() as unknown as {
    status: string | null;
    statusType: "info" | "warning" | "error" | "success" | "progress" | null;
    statusSpinner: boolean;
  };
  const status = statusObj?.status || null;

  if (!thinking && !status) {
    return null;
  }

  // Determine color based on status content (like bubbletea does)
  const isError = statusObj?.statusType === "error";
  const statusColor = isError ? Theme.stateFailed : Theme.stateActive;

  const showSpinner = !!thinking || !!statusObj?.statusSpinner;

  return (
    <Box>
      <Text>
        {showSpinner ? (
          <Text color={statusColor}>
            <Spinner type="dots" />
          </Text>
        ) : (
          <Text>{"  "}</Text>
        )}{" "}
        <Text color={statusColor} italic>
          {status || "Thinking..."}
        </Text>
      </Text>
    </Box>
  );
});
