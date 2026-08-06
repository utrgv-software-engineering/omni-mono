import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { useDismissedTaskIds, useTasks } from "../store/appStore.js";
import {
  STATUS_COLOR,
  STATUS_GLYPH,
  orderTasks,
  taskCounts,
} from "./task-model.js";
import { Theme } from "../theme.js";

const MAX_VISIBLE = 5;

// Task drawer (ctrl+a cycle). Ambient presence lives in the
// ActivityStrip; the full body renders only while this drawer is open.
//
// Ordering, counting and the glyph/colour palette live in `./task-model` so they
// stay exhaustive over the status set and can be tested without a render.
export const Tasks = React.memo<{ focused: boolean }>(({ focused }) => {
  const allTasks = useTasks();
  const dismissed = useDismissedTaskIds();
  if (!focused) return null;
  // Filter out completed tasks the user moved past on their last submit.
  const tasks = allTasks.filter(
    (t) => t.status !== "completed" || !dismissed.has(t.id),
  );
  if (!tasks || tasks.length === 0) {
    return (
      <Box paddingX={1} borderStyle="round" borderColor={Theme.borderAmbient}>
        <Text color={Theme.textChrome}>no tasks</Text>
      </Box>
    );
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
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.borderAmbient}
    >
      <Box>
        <Text color={Theme.textChrome}>Tasks </Text>
        <Text color={Theme.stateActive}>{counts.in_progress}</Text>
        <Text color={Theme.textChrome}> active · </Text>
        {counts.blocked > 0 && (
          <>
            <Text color={Theme.stateBlocked}>{counts.blocked}</Text>
            <Text color={Theme.textChrome}> blocked · </Text>
          </>
        )}
        <Text color={Theme.textChrome}>{counts.pending}</Text>
        <Text color={Theme.textChrome}> pending · </Text>
        <Text color={Theme.stateDone}>{counts.completed}</Text>
        <Text color={Theme.textChrome}> done</Text>
      </Box>
      {live && (
        <Box>
          <Text color={Theme.stateActive}>
            <Spinner type="dots" />
          </Text>
          <Text color={Theme.stateActive} italic>
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
            <Text color={Theme.textChrome}>#{t.id} </Text>
            <Text
              color={
                t.status === "completed"
                  ? Theme.textSecondary
                  : Theme.textPrimary
              }
              strikethrough={t.status === "completed"}
            >
              {t.subject}
            </Text>
            {blocked && (
              <Text color={Theme.stateBlocked}>
                {blockers.length > 0
                  ? ` › blocked by #${blockers.join(", #")}`
                  : " › blocked"}
              </Text>
            )}
          </Box>
        );
      })}
      {overflow > 0 && (
        <Text color={Theme.textChrome}>… +{overflow} more</Text>
      )}
    </Box>
  );
});
