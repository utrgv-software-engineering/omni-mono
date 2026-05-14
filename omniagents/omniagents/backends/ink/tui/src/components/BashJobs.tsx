import React, { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import Spinner from "ink-spinner";
import { useBashJobs, useThinking } from "../store/appStore.js";
import type { BashJobSummary } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";

const RUNNING_GLYPH = "◐";
const SUCCESS_GLYPH = "●";
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
  if (job.running) return TokyoNightTheme.info;
  return job.exit_code === 0 ? TokyoNightTheme.success : TokyoNightTheme.error;
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
  onTail: (job_id: string, lines?: number) => Promise<{
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
export const BashJobs = React.memo(({ focused, onUnfocus, onKill, onTail }: Props) => {
  const allJobs = useBashJobs();
  const thinking = useThinking();
  // Once the run ends, drop exited jobs so the panel quiets down — matches
  // the launcher's behavior and lets the dock disappear when nothing is left
  // running. Background jobs that outlive the run stay visible.
  const jobs = thinking ? allJobs : allJobs.filter((j) => j.running);

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
    ...exited.slice().sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0)),
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
  useInput(
    (_input, key) => {
      if (key.escape) {
        setLogsForJobId(null);
        setLogsError(null);
        return;
      }
      // Any other key dismisses logs and falls through to the next render's
      // row-list handler — feels natural ("press anything to close").
      setLogsForJobId(null);
    },
    { isActive: focused && logsForJobId !== null },
  );

  useInput(
    (input, key) => {
      if (key.escape) {
        onUnfocus();
        return;
      }
      if (visible.length === 0) return;
      if (key.upArrow) {
        setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return;
      }
      if (key.downArrow) {
        setSelectedIndex((prev) =>
          prev < visible.length - 1 ? prev + 1 : prev,
        );
        return;
      }
      const sel = visible[selectedIndex];
      if (!sel) return;
      if (input === "x") {
        if (!sel.running) {
          setStatusLine(`${sel.job_id} already exited`);
          return;
        }
        setStatusLine(`Killing ${sel.job_id}…`);
        onKill(sel.job_id)
          .then(() => setStatusLine(`Killed ${sel.job_id}`))
          .catch((e) =>
            setStatusLine(`Kill failed: ${(e as Error).message ?? String(e)}`),
          );
        return;
      }
      if (input === "l" || key.return) {
        loadLogs(sel.job_id);
        return;
      }
    },
    { isActive: focused && logsForJobId === null },
  );

  if (!jobs || jobs.length === 0) {
    return null;
  }

  // Collapsed form — only surfaced when something is actively running.
  // Mirrors StatusIndicator's "Thinking…" structure exactly so the spinner
  // glyph lands in the same column and the text picks up where the status
  // row's text would. Done/failed counts intentionally omitted: they're
  // history, not ambient signal. Ctrl+B opens the full panel for review.
  if (!focused) {
    if (running.length === 0) return null;
    const noun = running.length === 1 ? "background job" : "background jobs";
    return (
      // Match StatusIndicatorWrapper's paddingX={1} so the spinner here
      // aligns with the spinner in "⠋ Thinking..." one row up.
      <Box paddingX={1}>
        <Text>
          <Text color={TokyoNightTheme.info}>
            <Spinner type="dots" />
          </Text>{" "}
          <Text color={TokyoNightTheme.info}>{running.length}</Text>
          <Text color={TokyoNightTheme.defaultText}>
            {" "}
            {noun} running
          </Text>
          <Text color={TokyoNightTheme.help}>   Ctrl+B to manage</Text>
        </Text>
      </Box>
    );
  }

  const nowMs = Date.now();

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.info}
      paddingX={1}
      marginX={1}
    >
      <Box>
        <Text color={TokyoNightTheme.defaultText}>Bash jobs  </Text>
        <Text color={TokyoNightTheme.info}>{running.length}</Text>
        <Text color={TokyoNightTheme.defaultText}> running · </Text>
        <Text color={TokyoNightTheme.success}>{exited.length - failed.length}</Text>
        <Text color={TokyoNightTheme.defaultText}> done · </Text>
        <Text color={TokyoNightTheme.error}>{failed.length}</Text>
        <Text color={TokyoNightTheme.defaultText}> failed</Text>
      </Box>

      {visible.map((j, idx) => {
        const elapsed = formatElapsed(liveElapsedMs(j, nowMs));
        const tail = j.running ? elapsed : `exit ${j.exit_code} · ${elapsed}`;
        const isSelected = focused && idx === selectedIndex;
        const max = focused ? COMMAND_TRUNCATE_FOCUSED : COMMAND_TRUNCATE_UNFOCUSED;
        return (
          <Box key={j.job_id}>
            <Text color={TokyoNightTheme.info}>{isSelected ? "▶ " : "  "}</Text>
            <Text color={statusColor(j)}>{statusGlyph(j)} </Text>
            <Text color={TokyoNightTheme.defaultText}>{j.job_id} </Text>
            <Text
              color={
                j.running
                  ? TokyoNightTheme.inputText
                  : TokyoNightTheme.toolResult
              }
              strikethrough={!j.running}
            >
              {shortCommand(j.command, max)}
            </Text>
            <Text color={TokyoNightTheme.defaultText}>  {tail}</Text>
          </Box>
        );
      })}

      {overflow > 0 && (
        <Text color={TokyoNightTheme.defaultText}>… +{overflow} more</Text>
      )}

      {logsForJobId === null && (
        <Box>
          <Text color={TokyoNightTheme.help}>
            ↑↓ select · x kill · l logs · esc exit
          </Text>
        </Box>
      )}

      {statusLine && (
        <Box>
          <Text color={TokyoNightTheme.warning} italic>
            {statusLine}
          </Text>
        </Box>
      )}

      {logsForJobId && (
        <Box flexDirection="column" marginTop={1}>
          <Box>
            <Text color={TokyoNightTheme.info} bold>
              {logsForJobId}
            </Text>
            <Text color={TokyoNightTheme.defaultText}>
              {"  "}
              {logsLoading
                ? "loading…"
                : logsError
                ? "error"
                : `${logsText.split("\n").slice(-LOGS_PREVIEW_LINES).length} of ${logsTotalLines} lines`}
            </Text>
          </Box>
          {logsError ? (
            <Text color={TokyoNightTheme.error}>{logsError}</Text>
          ) : logsLoading ? (
            <Text color={TokyoNightTheme.help} italic>
              fetching tail…
            </Text>
          ) : logsText ? (
            <Box flexDirection="column">
              {logsText
                .split("\n")
                .slice(-LOGS_PREVIEW_LINES)
                .map((line, i) => (
                  <Text key={i} color={TokyoNightTheme.toolResult}>
                    {line}
                  </Text>
                ))}
            </Box>
          ) : (
            <Text color={TokyoNightTheme.help} italic>
              (no log output)
            </Text>
          )}
          <Box>
            <Text color={TokyoNightTheme.help}>
              any key to close · esc returns to list
            </Text>
          </Box>
        </Box>
      )}
    </Box>
  );
});
