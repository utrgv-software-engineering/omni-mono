import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import {
  useBashJobs,
  useDismissedJobIds,
  useThinking,
} from "../store/appStore.js";
import type { BashJobSummary } from "../store/appStore.js";
import { Theme } from "../theme.js";

const RUNNING_GLYPH = "◐";
const SUCCESS_GLYPH = "✔";
const FAILURE_GLYPH = "✗";

const MAX_VISIBLE = 5;
const COMMAND_TRUNCATE_FOCUSED = 50;
const COMMAND_TRUNCATE_UNFOCUSED = 60;
const LOGS_PREVIEW_LINES = 18;
const LOGS_TAIL_REQUEST_LINES = 200;

function statusGlyph(job: BashJobSummary): string {
  if (job.running) return RUNNING_GLYPH;
  return job.exit_code === 0 ? SUCCESS_GLYPH : FAILURE_GLYPH;
}

function statusColor(job: BashJobSummary): string {
  if (job.running) return Theme.stateActive;
  return job.exit_code === 0 ? Theme.stateDone : Theme.stateFailed;
}

function shortCommand(command: string, max: number): string {
  const oneLine = command.replace(/\s+/g, " ").trim();
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

function liveElapsedMs(job: BashJobSummary, nowMs: number): number {
  if (!job.running || !job.started_at) return job.wall_time_ms;
  const elapsed = nowMs - job.started_at * 1000;
  return Math.max(elapsed, job.wall_time_ms);
}

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  onKill: (job_id: string) => Promise<unknown>;
  onTail: (
    job_id: string,
    lines?: number,
  ) => Promise<{
    ok?: boolean;
    text?: string;
    total_lines?: number;
    error?: string;
    message?: string;
  }>;
};

// Docked background-jobs panel for the Ink TUI. Mirrors the Tasks dock for
// the unfocused presentation; adds keyboard-driven navigation when the
// parent App flips ``focused`` (Ctrl+B chord). Server-side kill/tail are
// reused via the same callbacks the web buttons use.
export const BashJobs = React.memo(
  ({ focused, onUnfocus, onKill, onTail }: Props) => {
    const allJobs = useBashJobs();
    const dismissed = useDismissedJobIds();
    const thinking = useThinking();
    // Filter out jobs the user moved past on their last submit; then apply
    // the idle-mode rule. Running jobs always stay visible regardless of
    // dismissal; failed exits (non-zero or null exit_code) stick around
    // until explicitly dismissed so a quietly-failed background job isn't
    // lost behind the next prompt.
    const live = allJobs.filter((j) => j.running || !dismissed.has(j.job_id));
    const jobs = thinking
      ? live
      : live.filter((j) => j.running || j.exit_code !== 0);

    // 1Hz tick while at least one job is running so elapsed time updates.
    const [, setNowTick] = useState(0);
    const anyRunning = jobs.some((j) => j.running);
    useEffect(() => {
      if (!anyRunning) return;
      const id = setInterval(() => setNowTick((n) => n + 1), 1000);
      return () => clearInterval(id);
    }, [anyRunning]);

    // Visual layout: running first, then most-recent exited. Same ordering as
    // the unfocused render so Ctrl+B doesn't reshuffle rows under the cursor.
    const running = jobs.filter((j) => j.running);
    const exited = jobs.filter((j) => !j.running);
    const failed = exited.filter((j) => (j.exit_code ?? 0) !== 0);
    const ordered = [
      ...running,
      ...exited
        .slice()
        .sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0)),
    ];
    const visible = ordered.slice(0, MAX_VISIBLE);
    const overflow = ordered.length - visible.length;

    // Selection state — clamped whenever the visible list changes so we don't
    // strand the cursor past the end after a job exits or another spawns.
    const [selectedIndex, setSelectedIndex] = useState(0);
    useEffect(() => {
      setSelectedIndex((prev) => {
        if (visible.length === 0) return 0;
        return Math.min(prev, visible.length - 1);
      });
    }, [visible.length]);

    // Inline log preview: when set, renders a Box with the tail of the job's
    // log file below the row list. Toggled by 'l' on the selected row;
    // dismissed by Esc or 'l' again.
    const [logsForJobId, setLogsForJobId] = useState<string | null>(null);
    const [logsText, setLogsText] = useState<string>("");
    const [logsTotalLines, setLogsTotalLines] = useState<number>(0);
    const [logsLoading, setLogsLoading] = useState<boolean>(false);
    const [logsError, setLogsError] = useState<string | null>(null);
    const [statusLine, setStatusLine] = useState<string | null>(null);

    // Auto-clear transient status messages after a short window.
    useEffect(() => {
      if (!statusLine) return;
      const id = setTimeout(() => setStatusLine(null), 2500);
      return () => clearTimeout(id);
    }, [statusLine]);

    const loadLogs = async (jobId: string) => {
      setLogsForJobId(jobId);
      setLogsLoading(true);
      setLogsError(null);
      setLogsText("");
      setLogsTotalLines(0);
      try {
        const res = await onTail(jobId, LOGS_TAIL_REQUEST_LINES);
        if (!res?.ok) {
          setLogsError(res?.message ?? res?.error ?? "Could not read logs");
        } else {
          setLogsText(res.text ?? "");
          setLogsTotalLines(res.total_lines ?? 0);
        }
      } catch (e) {
        setLogsError((e as Error).message ?? String(e));
      } finally {
        setLogsLoading(false);
      }
    };

    // Two keyboard layers, both gated on focused so they don't interfere with
    // the prompt when the panel isn't active.
    //
    //   1) Logs preview is open: any key dismisses it (escape returns focus to
    //      the row list; everything else falls through to row-list handling on
    //      the next keypress).
    //   2) Row list active: arrows move, x kills, l toggles log preview,
    //      Enter is an alias for l, Esc unfocuses the panel entirely.
    useKeypress(
      (_input, key) => {
        // Chords and Tab belong to the global layer — the log preview
        // must never swallow Ctrl+C or screen switching.
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false;
        if (key.escape) {
          setLogsForJobId(null);
          setLogsError(null);
          return true;
        }
        // Any other key dismisses logs and falls through to the next render's
        // row-list handler — feels natural ("press anything to close").
        setLogsForJobId(null);
        return true;
      },
      {
        isActive: focused && logsForJobId !== null,
        priority: KeypressPriority.Critical,
      },
    );

    useKeypress(
      (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
        if (key.escape) {
          onUnfocus();
          return true;
        }
        if (visible.length === 0) return false;
        if (key.upArrow) {
          setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
          return true;
        }
        if (key.downArrow) {
          setSelectedIndex((prev) =>
            prev < visible.length - 1 ? prev + 1 : prev,
          );
          return true;
        }
        const sel = visible[selectedIndex];
        if (!sel) return false;
        if (input === "x") {
          if (!sel.running) {
            setStatusLine(`${sel.job_id} already exited`);
            return true;
          }
          setStatusLine(`Killing ${sel.job_id}…`);
          onKill(sel.job_id)
            .then(() => setStatusLine(`Killed ${sel.job_id}`))
            .catch((e) =>
              setStatusLine(
                `Kill failed: ${(e as Error).message ?? String(e)}`,
              ),
            );
          return true;
        }
        if (input === "l" || key.return) {
          loadLogs(sel.job_id);
          return true;
        }
        return false;
      },
      {
        isActive: focused && logsForJobId === null,
        priority: KeypressPriority.High,
      },
    );

    // Drawer model: ambient presence (running count, failed-job warning)
    // is the ActivityStrip's job; the body renders only while focused.
    if (!focused) {
      return null;
    }
    if (!jobs || jobs.length === 0) {
      return (
        <Box paddingX={1} borderStyle="round" borderColor={Theme.borderAmbient}>
          <Text color={Theme.textChrome}>no background jobs</Text>
        </Box>
      );
    }

    const nowMs = Date.now();

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Bash jobs </Text>
          <Text color={Theme.stateActive}>{running.length}</Text>
          <Text color={Theme.textChrome}> running · </Text>
          <Text color={Theme.stateDone}>
            {exited.length - failed.length}
          </Text>
          <Text color={Theme.textChrome}> done · </Text>
          <Text color={Theme.stateFailed}>{failed.length}</Text>
          <Text color={Theme.textChrome}> failed</Text>
        </Box>

        {visible.map((j, idx) => {
          const elapsed = formatElapsed(liveElapsedMs(j, nowMs));
          const tail = j.running ? elapsed : `exit ${j.exit_code} · ${elapsed}`;
          const isSelected = focused && idx === selectedIndex;
          const max = focused
            ? COMMAND_TRUNCATE_FOCUSED
            : COMMAND_TRUNCATE_UNFOCUSED;
          return (
            <Box key={j.job_id}>
              <Text color={Theme.focus}>
                {isSelected ? "▶ " : "  "}
              </Text>
              <Text color={statusColor(j)}>{statusGlyph(j)} </Text>
              <Text color={Theme.textChrome}>{j.job_id} </Text>
              <Text
                color={
                  j.running
                    ? Theme.textPrimary
                    : Theme.textSecondary
                }
                strikethrough={!j.running}
              >
                {shortCommand(j.command, max)}
              </Text>
              <Text color={Theme.textChrome}> {tail}</Text>
            </Box>
          );
        })}

        {overflow > 0 && (
          <Text color={Theme.textChrome}>… +{overflow} more</Text>
        )}

        {logsForJobId === null && (
          <Box>
            <Text color={Theme.textChrome}>
              ↑↓ select · x kill · l logs · esc exit
            </Text>
          </Box>
        )}

        {statusLine && (
          <Box>
            <Text color={Theme.stateBlocked} italic>
              {statusLine}
            </Text>
          </Box>
        )}

        {logsForJobId && (
          <Box flexDirection="column" marginTop={1}>
            <Box>
              <Text color={Theme.stateActive} bold>
                {logsForJobId}
              </Text>
              <Text color={Theme.textChrome}>
                {"  "}
                {logsLoading
                  ? "loading…"
                  : logsError
                    ? "error"
                    : `${logsText.split("\n").slice(-LOGS_PREVIEW_LINES).length} of ${logsTotalLines} lines`}
              </Text>
            </Box>
            {logsError ? (
              <Text color={Theme.stateFailed}>{logsError}</Text>
            ) : logsLoading ? (
              <Text color={Theme.textChrome} italic>
                fetching tail…
              </Text>
            ) : logsText ? (
              <Box flexDirection="column">
                {logsText
                  .split("\n")
                  .slice(-LOGS_PREVIEW_LINES)
                  .map((line, i) => (
                    <Text key={i} color={Theme.textSecondary}>
                      {line}
                    </Text>
                  ))}
              </Box>
            ) : (
              <Text color={Theme.textChrome} italic>
                (no log output)
              </Text>
            )}
            <Box>
              <Text color={Theme.textChrome}>
                any key to close · esc returns to list
              </Text>
            </Box>
          </Box>
        )}
      </Box>
    );
  },
);
