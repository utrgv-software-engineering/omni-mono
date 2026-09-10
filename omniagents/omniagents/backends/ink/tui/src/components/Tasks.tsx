import React from "react";
import { Box, Text } from "ink";
import { Clip, Fixed, oneLine } from "./shared/drawer-layout.js";
import Spinner from "ink-spinner";
import { useDismissedTaskIds, useTasks } from "../store/appStore.js";
import {
  STATUS_COLOR,
  STATUS_GLYPH,
  orderTasks,
  taskCounts,
} from "./task-model.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

const MAX_VISIBLE = 5;

// Task drawer (/tasks). Ambient presence lives in the
// ActivityStrip; the full body renders only while this drawer is open.
//
// Ordering, counting and the glyph/colour palette live in `./task-model` so they
// stay exhaustive over the status set and can be tested without a render.
export const Tasks = themedMemo<{ focused: boolean }>(({ focused }) => {
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
          <Clip>
            <Text color={Theme.stateActive} italic wrap="truncate-end">
              {" "}
              {oneLine(live.activeForm || live.subject)}
            </Text>
          </Clip>
        </Box>
      )}
      {visible.map((t) => {
        const blockers = t.blockedBy ?? [];
        const blocked = t.status === "blocked" || blockers.length > 0;
        return (
          <Box key={t.id}>
            <Fixed>
              <Text color={STATUS_COLOR[t.status] ?? STATUS_COLOR.pending}>
                {STATUS_GLYPH[t.status] ?? STATUS_GLYPH.pending}{" "}
              </Text>
              <Text color={Theme.textChrome}>#{t.id} </Text>
            </Fixed>
            <Clip>
              <Text
                color={
                  t.status === "completed"
                    ? Theme.textSecondary
                    : Theme.textPrimary
                }
                strikethrough={t.status === "completed"}
                wrap="truncate-end"
              >
                {oneLine(t.subject)}
              </Text>
            </Clip>
            {blocked && (
              <Fixed>
                <Text color={Theme.stateBlocked}>
                  {blockers.length > 0
                    ? ` › blocked by #${blockers.join(", #")}`
                    : " › blocked"}
                </Text>
              </Fixed>
            )}
          </Box>
        );
      })}
      {overflow > 0 && <Text color={Theme.textChrome}>… +{overflow} more</Text>}
    </Box>
  );
});
