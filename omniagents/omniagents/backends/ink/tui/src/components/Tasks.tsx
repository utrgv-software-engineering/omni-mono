import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import {
  useDismissedTaskIds,
  useTasks,
  useThinking,
} from "../store/appStore.js";
import {
  STATUS_COLOR,
  STATUS_GLYPH,
  orderTasks,
  taskCounts,
} from "./tasks.js";
import { TokyoNightTheme } from "../theme.js";

const MAX_VISIBLE = 5;

// Docked task list rendered just above the input prompt. Returns null
// when there are no tasks so the message area reclaims the space.
//
// Ordering, counting and the glyph/colour palette live in `./tasks` so they
// stay exhaustive over the status set and can be tested without a render.
export const Tasks = React.memo(() => {
  const allTasks = useTasks();
  const dismissed = useDismissedTaskIds();
  const thinking = useThinking();
  // Filter out completed tasks the user moved past on their last submit,
  // then apply the idle-mode "non-completed only" rule so the dock
  // disappears between runs.
  const undismissed = allTasks.filter(
    (t) => t.status !== "completed" || !dismissed.has(t.id),
  );
  const tasks = thinking
    ? undismissed
    : undismissed.filter((t) => t.status !== "completed");
  if (!tasks || tasks.length === 0) {
    return null;
  }

  // Show the active work first (in_progress, then blocked, then pending),
  // completed last so the dock doesn't pile up. Completed are still kept in
  // the underlying state.
  const ordered = orderTasks(tasks);
  const visible = ordered.slice(0, MAX_VISIBLE);
  const overflow = ordered.length - visible.length;
  const counts = taskCounts(tasks);

  // First in_progress task drives the live status line above the rows.
  const live = ordered.find((t) => t.status === "in_progress");

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
    >
      <Box>
        <Text color={TokyoNightTheme.defaultText}>Tasks </Text>
        <Text color={TokyoNightTheme.info}>{counts.in_progress}</Text>
        <Text color={TokyoNightTheme.defaultText}> active · </Text>
        {counts.blocked > 0 && (
          <>
            <Text color={TokyoNightTheme.warning}>{counts.blocked}</Text>
            <Text color={TokyoNightTheme.defaultText}> blocked · </Text>
          </>
        )}
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
        const blockers = t.blockedBy ?? [];
        const blocked = t.status === "blocked" || blockers.length > 0;
        return (
          <Box key={t.id}>
            <Text color={STATUS_COLOR[t.status] ?? STATUS_COLOR.pending}>
              {STATUS_GLYPH[t.status] ?? STATUS_GLYPH.pending}{" "}
            </Text>
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
                {blockers.length > 0
                  ? ` › blocked by #${blockers.join(", #")}`
                  : " › blocked"}
              </Text>
            )}
          </Box>
        );
      })}
      {overflow > 0 && (
        <Text color={TokyoNightTheme.defaultText}>… +{overflow} more</Text>
      )}
    </Box>
  );
});
