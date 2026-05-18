import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import {
  useDismissedWorkerIds,
  useThinking,
  useWorkers,
} from "../store/appStore.js";
import type { WorkerSummary } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

const SUCCESS_GLYPH = "●";
const FAILURE_GLYPH = "✗";

const MAX_VISIBLE = 5;
const TASK_TRUNCATE = 60;

function statusColor(worker: WorkerSummary): string {
  if (worker.status === "running") return TokyoNightTheme.info;
  if (worker.status === "completed") return TokyoNightTheme.success;
  return TokyoNightTheme.error;
}

function shortTask(task: string, max: number): string {
  const oneLine = task.replace(/\s+/g, " ").trim();
  if (oneLine.length <= max) return oneLine;
  return oneLine.slice(0, max - 1) + "…";
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rem = Math.floor(seconds - minutes * 60);
  return `${minutes}m${rem.toString().padStart(2, "0")}s`;
}

function liveElapsedMs(worker: WorkerSummary, nowMs: number): number {
  if (worker.status === "running" && worker.started_at) {
    return Math.max(nowMs - worker.started_at * 1000, worker.wall_time_ms ?? 0);
  }
  return worker.wall_time_ms ?? 0;
}

// Docked workers panel for the Ink TUI. Mirrors Tasks.tsx in shape —
// pure display, no keyboard interaction. The agent can manage workers
// via the spawn_worker / wait_for_workers / worker_close tools; the
// panel exists so the user can see what's running. Hidden when empty.
export const Workers = React.memo(() => {
  const allWorkers = useWorkers();
  const dismissed = useDismissedWorkerIds();
  const thinking = useThinking();
  // Filter out workers the user moved past on their last submit; then
  // apply the idle-mode "running only" rule so the panel quiets down
  // between runs. Running workers (e.g. long-lived ones spawned by
  // /goal) always stay visible regardless of dismissal.
  const live = allWorkers.filter(
    (w) => w.status === "running" || !dismissed.has(w.worker_id),
  );
  const workers = thinking ? live : live.filter((w) => w.status === "running");

  // 1Hz tick while at least one worker is running so elapsed time updates.
  const [, setNowTick] = useState(0);
  const anyRunning = workers.some((w) => w.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const id = setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [anyRunning]);

  if (!workers || workers.length === 0) {
    return null;
  }

  const running = workers.filter((w) => w.status === "running");
  const exited = workers.filter((w) => w.status !== "running");
  const succeeded = exited.filter((w) => w.status === "completed").length;
  const failed = exited.length - succeeded;

  const ordered = [
    ...running,
    ...exited
      .slice()
      .sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0)),
  ];
  const visible = ordered.slice(0, MAX_VISIBLE);
  const overflow = ordered.length - visible.length;
  const nowMs = Date.now();

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
    >
      <Box>
        <Text color={TokyoNightTheme.defaultText}>Workers  </Text>
        <Text color={TokyoNightTheme.info}>{running.length}</Text>
        <Text color={TokyoNightTheme.defaultText}> running · </Text>
        <Text color={TokyoNightTheme.success}>{succeeded}</Text>
        <Text color={TokyoNightTheme.defaultText}> done</Text>
        {failed > 0 && (
          <>
            <Text color={TokyoNightTheme.defaultText}> · </Text>
            <Text color={TokyoNightTheme.error}>{failed}</Text>
            <Text color={TokyoNightTheme.defaultText}> failed</Text>
          </>
        )}
      </Box>
      {visible.map((w) => {
        const elapsed = formatElapsed(liveElapsedMs(w, nowMs));
        const tail =
          w.status === "running" ? elapsed : `${w.status} · ${elapsed}`;
        return (
          <Box key={w.worker_id}>
            <Text color={statusColor(w)}>
              {w.status === "running" ? (
                <Spinner type="dots" />
              ) : w.status === "completed" ? (
                SUCCESS_GLYPH
              ) : (
                FAILURE_GLYPH
              )}
            </Text>
            <Text color={TokyoNightTheme.defaultText}> {w.worker_id} </Text>
            <Text
              color={
                w.status === "running"
                  ? TokyoNightTheme.inputText
                  : TokyoNightTheme.toolResult
              }
            >
              {shortTask(w.task, TASK_TRUNCATE)}
            </Text>
            <Text color={TokyoNightTheme.defaultText}>  {tail}</Text>
          </Box>
        );
      })}
      {overflow > 0 && (
        <Text color={TokyoNightTheme.defaultText}>… +{overflow} more</Text>
      )}
    </Box>
  );
});
