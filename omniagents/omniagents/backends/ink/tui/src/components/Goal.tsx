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
// Active:        ⠋ goal · refactor foo to use bar · turn 3/10
// Retrying:      ⠋ goal · refactor foo to use bar · turn 3/10 · retry 1/3 after error
// Completed:     ● goal · refactor foo to use bar · completed at turn 4
// Cancelled:     ✗ goal · refactor foo to use bar · cancelled at turn 2
export const Goal = React.memo(() => {
  const goal = useGoal();
  if (!goal) return null;

  const isActive = goal.status === "active";
  const isCompleted = goal.status === "completed";
  const retrying = isActive && goal.attempt > 0;

  const glyphColor = isCompleted
    ? TokyoNightTheme.success
    : goal.status === "cancelled"
      ? TokyoNightTheme.error
      : TokyoNightTheme.info;

  return (
    <Box paddingX={1} marginX={1}>
      <Text color={glyphColor}>
        {isActive ? <Spinner type="dots" /> : isCompleted ? "●" : "✗"}
      </Text>
      <Text color={TokyoNightTheme.defaultText}> goal </Text>
      <Text color={TokyoNightTheme.toolResult}>· </Text>
      <Text color={TokyoNightTheme.inputText}>
        {shortGoal(goal.goal, GOAL_TRUNCATE)}
      </Text>
      <Text color={TokyoNightTheme.toolResult}> · </Text>
      {isActive ? (
        <>
          <Text color={TokyoNightTheme.defaultText}>turn </Text>
          <Text color={TokyoNightTheme.info}>{goal.turn}</Text>
          <Text color={TokyoNightTheme.defaultText}>
            /{goal.max_turns}
          </Text>
          {retrying && (
            <>
              <Text color={TokyoNightTheme.toolResult}> · </Text>
              <Text color={TokyoNightTheme.warning}>
                retry {goal.attempt}/{goal.max_attempts}
              </Text>
              {goal.last_reason && (
                <Text color={TokyoNightTheme.defaultText}>
                  {" "}after {goal.last_reason}
                </Text>
              )}
            </>
          )}
        </>
      ) : (
        <Text color={TokyoNightTheme.defaultText}>
          {goal.status} at turn {goal.turn}
        </Text>
      )}
    </Box>
  );
});
