import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { useActions, useSubagentEvents, useSubagents } from "../store/appStore.js";
import type { SubagentSummary } from "../store/appStore.js";
import { windowStart } from "./model-picker-model.js";
import { clampScroll } from "./git-model.js";
import { buildWorkerDetail } from "./agents-model.js";
import { EmptyState } from "./shared/EmptyState.js";
import { Theme } from "../theme.js";

const WIDE_BREAKPOINT = 90;
const LIST_WIDTH = 46;
const NARROW_LIST_ROWS = 8;
const DETAIL_PAGE = 12;

type Props = {
  active: boolean;
  height: number;
  onExit: () => void;
};

const EMPTY_EVENTS: never[] = [];

function statusColor(worker: SubagentSummary): string {
  if (worker.status === "running") return Theme.stateActive;
  if (worker.status === "completed") return Theme.stateDone;
  if (worker.status === "cancelled") return Theme.stateBlocked;
  return Theme.stateFailed;
}

function elapsed(worker: SubagentSummary, nowMs: number): string {
  const ms =
    worker.status === "running" && worker.started_at
      ? Math.max(nowMs - worker.started_at * 1000, worker.wall_time_ms ?? 0)
      : (worker.wall_time_ms ?? 0);
  const seconds = ms / 1000;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

function shortTask(task: string, max = 28): string {
  const oneLine = task.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

// The Agents screen (tab 3): subagent supervision, full height. Worker
// list on one side, the selected worker's detail — full task, result or
// error text, uncapped and scrollable — on the other. Same spatial
// focus model as Review: ←/→ (h/l) switch panes, ↑↓ is contextual
// (worker cursor on the list, scrolling on the detail), enter opens,
// esc pops. Push-driven from the workers snapshots; no polling.
export const AgentsScreen = ({ active, height, onExit }: Props) => {
  const { stdout } = useStdout();
  const width = stdout?.columns ?? 80;
  const wide = width >= WIDE_BREAKPOINT;
  const workers = useSubagents();
  const subagentEvents = useSubagentEvents();
  const actions = useActions();

  const [index, setIndex] = useState(0);
  const [detailScroll, setDetailScroll] = useState(0);
  // Which pane owns ↑↓. Only meaningful in the wide split — narrow
  // mode stacks both panes and keeps the combined keys.
  const [pane, setPane] = useState<"list" | "detail">("list");

  // Running first, then most recently started.
  const ordered = useMemo(() => {
    const running = workers.filter((w) => w.status === "running");
    const done = workers
      .filter((w) => w.status !== "running")
      .sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
    return [...running, ...done];
  }, [workers]);

  useEffect(() => {
    setIndex((prev) =>
      ordered.length === 0 ? 0 : Math.min(prev, ordered.length - 1),
    );
  }, [ordered.length]);

  useEffect(() => {
    if (active) setPane("list");
  }, [active]);

  const selected = ordered[index] ?? null;

  // 1Hz tick for live elapsed while anything runs and the screen shows.
  const [, setNowTick] = useState(0);
  const anyRunning = ordered.some((w) => w.status === "running");
  useEffect(() => {
    if (!active || !anyRunning) return;
    const id = setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active, anyRunning]);

  // Detail text is pre-wrapped to the pane's width so scrolling counts
  // the rows actually on screen — nothing in the pane truncates.
  const detailWidth = wide
    ? Math.max(20, width - LIST_WIDTH - 2)
    : Math.max(20, width - 1);
  const selectedEvents = selected
    ? (subagentEvents[selected.subagent_id] ?? EMPTY_EVENTS)
    : EMPTY_EVENTS;
  const detailLines = useMemo(
    () =>
      selected ? buildWorkerDetail(selected, detailWidth, selectedEvents) : [],
    [selected, detailWidth, selectedEvents],
  );

  useEffect(() => {
    setDetailScroll(0);
  }, [selected?.subagent_id]);

  // Live feed follows the tail while the worker runs — but never yanks
  // the view while the user is reading (detail pane focused). Keyed to
  // the line count only: focus/status changes alone must not re-scroll.
  useEffect(() => {
    if (!selected || selected.status !== "running") return;
    if (wide && pane === "detail") return;
    setDetailScroll(
      clampScroll(detailLines.length, detailLines.length, detailWindow),
    );
  }, [detailLines.length]);

  // Publish the focus path to the status bar: the screen while the
  // list owns focus, the worker being read once the detail does.
  useEffect(() => {
    if (!active) return;
    actions.setStatusContext(
      wide && pane === "detail" && selected
        ? `agents · ${shortTask(selected.task)}`
        : "agents",
    );
    return () => actions.setStatusContext(null);
  }, [active, wide, pane, selected, actions]);

  const detailFocused = wide && pane === "detail";

  const bodyHeight = Math.max(4, height - 1);
  const listRows = wide
    ? Math.max(3, bodyHeight - 4) // border + title + overflow rows
    : Math.min(NARROW_LIST_ROWS, Math.max(3, Math.floor(bodyHeight / 3)));
  // Wide mode reserves one top row on the detail column so its first
  // text line sits level with the sidebar's title row (the sidebar's
  // border occupies row 0), plus one row for the range counter.
  const detailWindow = wide
    ? Math.max(3, bodyHeight - 2)
    : Math.max(3, bodyHeight - listRows - 2);

  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.escape || input === "q") {
        // Esc pops one level: detail pane → list → chat.
        if (detailFocused) {
          setPane("list");
        } else {
          onExit();
        }
        return true;
      }
      // ←/→ (h/l) move focus between the list and the detail.
      if (wide && (key.leftArrow || input === "h")) {
        setPane("list");
        return true;
      }
      if (wide && (key.rightArrow || input === "l")) {
        setPane("detail");
        return true;
      }
      // Enter on a worker opens (focuses) its detail.
      if (key.return && wide && pane === "list" && selected) {
        setPane("detail");
        setDetailScroll(0);
        return true;
      }
      // ↑↓/j/k: worker cursor when the list owns focus (or in narrow
      // mode), detail scrolling when the detail does.
      if (key.downArrow || input === "j") {
        if (detailFocused) {
          setDetailScroll((prev) =>
            clampScroll(prev + 1, detailLines.length, detailWindow),
          );
        } else if (ordered.length > 0) {
          setIndex((prev) => Math.min(ordered.length - 1, prev + 1));
        }
        return true;
      }
      if (key.upArrow || input === "k") {
        if (detailFocused) {
          setDetailScroll((prev) =>
            clampScroll(prev - 1, detailLines.length, detailWindow),
          );
        } else if (ordered.length > 0) {
          setIndex((prev) => Math.max(0, prev - 1));
        }
        return true;
      }
      // PgUp/PgDn (and J/K) always page the detail.
      if (key.pageDown || input === "J") {
        setDetailScroll((prev) =>
          clampScroll(prev + DETAIL_PAGE, detailLines.length, detailWindow),
        );
        return true;
      }
      if (key.pageUp || input === "K") {
        setDetailScroll((prev) =>
          clampScroll(prev - DETAIL_PAGE, detailLines.length, detailWindow),
        );
        return true;
      }
      return false;
    },
    { isActive: active, priority: KeypressPriority.High },
  );

  if (!active) return null;

  const nowMs = Date.now();
  const listStart = windowStart(index, ordered.length, listRows);
  const visible = ordered.slice(listStart, listStart + listRows);
  const visibleDetail = detailLines.slice(
    detailScroll,
    detailScroll + detailWindow,
  );

  const rows = visible.map((worker, i) => {
    const isSelected = listStart + i === index;
    return (
      <Box key={worker.subagent_id}>
        <Text color={Theme.focus}>{isSelected ? "▶ " : "  "}</Text>
        {worker.status === "running" ? (
          <Text color={Theme.stateActive}>
            <Spinner type="dots" />{" "}
          </Text>
        ) : (
          <Text color={statusColor(worker)}>
            {worker.status === "completed" ? "✔ " : "✗ "}
          </Text>
        )}
        {/* minWidth 0 — see ReviewScreen sidebar: overflowing rows
            otherwise wrap to height 2 and eat the cursor. */}
        <Box flexGrow={1} minWidth={0}>
          <Text
            color={isSelected ? Theme.textPrimary : Theme.textChrome}
            bold={isSelected}
            wrap="truncate-end"
          >
            {worker.task.replace(/\s+/g, " ").trim()}
          </Text>
        </Box>
        <Text color={Theme.textChrome}> {elapsed(worker, nowMs)}</Text>
      </Box>
    );
  });

  const overflow = ordered.length > listRows && (
    <Text color={Theme.textChrome}>
      {listStart + 1}–{Math.min(listStart + listRows, ordered.length)} of{" "}
      {ordered.length}
    </Text>
  );

  // Wide: a bordered sidebar (same chrome as Review's file list) whose
  // border color shows which pane owns the keyboard.
  const list = wide ? (
    <Box
      flexDirection="column"
      width={LIST_WIDTH}
      flexShrink={0}
      height={bodyHeight}
      paddingX={1}
      borderStyle="round"
      borderColor={pane === "list" ? Theme.borderFocus : Theme.borderAmbient}
    >
      <Text color={Theme.textChrome}>
        agents <Text color={Theme.stateActive}>{ordered.length}</Text>
      </Text>
      {rows}
      {overflow}
    </Box>
  ) : (
    <Box flexDirection="column" height={listRows + 1}>
      {rows}
      {overflow}
    </Box>
  );

  const detail = (
    <Box
      flexDirection="column"
      flexGrow={1}
      height={wide ? bodyHeight : undefined}
      paddingLeft={wide ? 1 : 0}
      paddingTop={wide ? 1 : 0}
    >
      {visibleDetail.map((line, i) => (
        <Text
          key={detailScroll + i}
          color={line.color}
          bold={line.bold}
          wrap="truncate-end"
        >
          {/* Empty Text collapses to zero height in Ink — blank rows
              must still occupy their row or the scroll math (which
              counts them) diverges from the pixels. */}
          {line.text.length > 0 ? line.text : " "}
        </Text>
      ))}
      {selected && detailLines.length > detailWindow && (
        <Text color={Theme.textChrome}>
          {detailScroll + 1}–
          {Math.min(detailScroll + detailWindow, detailLines.length)} of{" "}
          {detailLines.length}
        </Text>
      )}
    </Box>
  );

  return (
    <Box flexDirection="column" height={height} width="100%" flexGrow={1}>
      {ordered.length === 0 ? (
        <EmptyState
          title="no subagents yet"
          lines={[
            "workers and tool agents the agent spawns appear",
            "here with live activity and full results",
          ]}
          actions={[
            { keys: "↑↓", label: "select a worker" },
            { keys: "enter", label: "read its result" },
            { keys: "esc", label: "back to chat" },
          ]}
        />
      ) : wide ? (
        <Box flexDirection="row" flexGrow={1}>
          {list}
          {detail}
        </Box>
      ) : (
        <Box flexDirection="column" flexGrow={1}>
          {list}
          {detail}
        </Box>
      )}

      <Text color={Theme.textChrome}>
        {detailFocused
          ? "↑↓ scroll · ←/esc list"
          : wide
            ? "↑↓ select · enter detail · esc chat"
            : "↑↓ select · J/K scroll · esc chat"}
      </Text>
    </Box>
  );
};
