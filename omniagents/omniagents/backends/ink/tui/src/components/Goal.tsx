import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useGoal } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

const GOAL_TRUNCATE = 60;

function shortGoal(goal: string, max: number): string {
  const oneLine = goal.replace(/\s+/g, " ").trim();
  if (oneLine.length <= max) return oneLine;
  return oneLine.slice(0, max - 1) + "…";
}

// Compact single-line docked panel for the /goal autopilot loop.
// Renders nothing when no goal is set.
//
// Active:    ⠋ goal · refactor foo to use bar
// Completed: ● goal · refactor foo to use bar · completed
// Cancelled: ✗ goal · refactor foo to use bar · cancelled
export const Goal = React.memo(() => {
  const goal = useGoal();
  if (!goal) return null;

  const isActive = goal.status === "active";
  const isCompleted = goal.status === "completed";

  const glyphColor = isCompleted
    ? TokyoNightTheme.success
    : goal.status === "cancelled"
      ? TokyoNightTheme.error
      : TokyoNightTheme.info;

  return (
    <Box>
      <Text color={glyphColor}>
        {isActive ? <Spinner type="dots" /> : isCompleted ? "●" : "✗"}
      </Text>
      <Text color={TokyoNightTheme.defaultText}> goal </Text>
      <Text color={TokyoNightTheme.toolResult}>· </Text>
      <Text color={TokyoNightTheme.inputText}>
        {shortGoal(goal.goal, GOAL_TRUNCATE)}
      </Text>
      {!isActive && (
        <>
          <Text color={TokyoNightTheme.toolResult}> · </Text>
          <Text color={TokyoNightTheme.defaultText}>{goal.status}</Text>
        </>
      )}
    </Box>
  );
});
