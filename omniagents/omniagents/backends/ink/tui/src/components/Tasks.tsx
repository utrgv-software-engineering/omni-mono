import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useTasks } from "../store/appStore.js";
import type { TaskSummary } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

const STATUS_GLYPH: Record<TaskSummary["status"], string> = {
  pending: "○",
  in_progress: "◐",
  completed: "●",
};

const STATUS_COLOR: Record<TaskSummary["status"], string> = {
  pending: TokyoNightTheme.defaultText,
  in_progress: TokyoNightTheme.info,
  completed: TokyoNightTheme.success,
};

const MAX_VISIBLE = 8;

// Docked task list rendered just above the input prompt. Returns null
// when there are no tasks so the message area reclaims the space.
export const Tasks = React.memo(() => {
  const tasks = useTasks();
  if (!tasks || tasks.length === 0) {
    return null;
  }

  // Show the active work first (in_progress, then pending), drop
  // completed tasks once the list grows past MAX_VISIBLE so the dock
  // doesn't pile up. Completed are still kept in the underlying state.
  const active = tasks.filter((t) => t.status !== "completed");
  const completed = tasks.filter((t) => t.status === "completed");
  const ordered = [
    ...active.filter((t) => t.status === "in_progress"),
    ...active.filter((t) => t.status === "pending"),
    ...completed,
  ];
  const visible = ordered.slice(0, MAX_VISIBLE);
  const overflow = ordered.length - visible.length;

  const counts = {
    pending: tasks.filter((t) => t.status === "pending").length,
    in_progress: tasks.filter((t) => t.status === "in_progress").length,
    completed: completed.length,
  };

  // First in_progress task drives the live status line above the rows.
  const live = ordered.find((t) => t.status === "in_progress");

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
      paddingX={1}
      marginX={1}
    >
      <Box>
        <Text color={TokyoNightTheme.defaultText}>
          Tasks  </Text>
        <Text color={TokyoNightTheme.info}>{counts.in_progress}</Text>
        <Text color={TokyoNightTheme.defaultText}> active · </Text>
        <Text color={TokyoNightTheme.defaultText}>{counts.pending}</Text>
        <Text color={TokyoNightTheme.defaultText}> pending · </Text>
        <Text color={TokyoNightTheme.success}>{counts.completed}</Text>
        <Text color={TokyoNightTheme.defaultText}> done</Text>
      </Box>
      {live && (
        <Box>
          <Text color={TokyoNightTheme.info}>
            <Spinner type="dots" />
          </Text>
          <Text color={TokyoNightTheme.info} italic>
            {" "}
            {live.activeForm || live.subject}
          </Text>
        </Box>
      )}
      {visible.map((t) => {
        const blocked = (t.blockedBy ?? []).length > 0;
        return (
          <Box key={t.id}>
            <Text color={STATUS_COLOR[t.status]}>{STATUS_GLYPH[t.status]} </Text>
            <Text color={TokyoNightTheme.defaultText}>#{t.id} </Text>
            <Text
              color={
                t.status === "completed"
                  ? TokyoNightTheme.toolResult
                  : TokyoNightTheme.inputText
              }
              strikethrough={t.status === "completed"}
            >
              {t.subject}
            </Text>
            {blocked && (
              <Text color={TokyoNightTheme.warning}>
                {" "}› blocked by #{t.blockedBy!.join(", #")}
              </Text>
            )}
          </Box>
        );
      })}
      {overflow > 0 && (
        <Text color={TokyoNightTheme.defaultText}>
          … +{overflow} more
        </Text>
      )}
    </Box>
  );
});
