import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { useLoops } from "../store/appStore.js";
import type { LoopTaskSnapshot } from "@omniagents/client/session/panel-types";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Theme, roleMap } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  onStop: (id: string) => Promise<unknown>;
  onClear: () => Promise<unknown>;
};

const MAX_VISIBLE = 6;

/** Active loops first (soonest to fire first), then the rest, newest first. */
export function orderLoops(
  loops: readonly LoopTaskSnapshot[],
): LoopTaskSnapshot[] {
  const active = loops
    .filter((l) => l.status === "active")
    .sort(
      (a, b) => (a.next_fire_at ?? Infinity) - (b.next_fire_at ?? Infinity),
    );
  const rest = loops
    .filter((l) => l.status !== "active")
    .sort((a, b) => b.created_at - a.created_at);
  return [...active, ...rest];
}

export function describeSchedule(loop: LoopTaskSnapshot): string {
  if (loop.mode === "fixed_cron" && loop.cron) return loop.cron;
  if (loop.mode === "fixed_interval" && loop.interval_seconds) {
    const s = loop.interval_seconds;
    return s % 3600 === 0
      ? `every ${s / 3600}h`
      : s % 60 === 0
        ? `every ${s / 60}m`
        : `every ${s}s`;
  }
  return "when the agent asks";
}

function untilNext(loop: LoopTaskSnapshot, nowMs: number): string {
  if (loop.status !== "active" || !loop.next_fire_at) return loop.status;
  const seconds = Math.max(0, Math.round(loop.next_fire_at - nowMs / 1000));
  return seconds < 60 ? `in ${seconds}s` : `in ${Math.round(seconds / 60)}m`;
}

const STATUS_COLOR: Record<LoopTaskSnapshot["status"], string> = roleMap({
  active: "stateActive",
  completed: "stateDone",
  cancelled: "stateFailed",
  expired: "textSecondary",
});

/**
 * The loop drawer: what `/loop` with nothing after it opens. Lists the
 * session's scheduled prompts with a cursor; `x` stops the selected one,
 * `c` clears them all. `/loop [interval] <prompt>` still starts one.
 */
export const LoopPanel = themedMemo(
  ({ focused, onUnfocus, onStop, onClear }: Props) => {
    const loops = useLoops();
    const ordered = orderLoops(loops);
    const visible = ordered.slice(0, MAX_VISIBLE);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [, setTick] = useState(0);

    useEffect(() => {
      setSelectedIndex((prev) =>
        visible.length === 0 ? 0 : Math.min(prev, visible.length - 1),
      );
    }, [visible.length]);
    useEffect(() => {
      if (!focused || !ordered.some((l) => l.status === "active")) return;
      const id = setInterval(() => setTick((n) => n + 1), 1000);
      return () => clearInterval(id);
    }, [focused, ordered]);
    useEffect(() => {
      if (!statusLine) return;
      const id = setTimeout(() => setStatusLine(null), 2500);
      return () => clearTimeout(id);
    }, [statusLine]);

    useKeypress(
      (input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false;
        if (key.escape) {
          onUnfocus();
          return true;
        }
        if (visible.length === 0) return false;
        if (key.upArrow) {
          setSelectedIndex((prev) => Math.max(0, prev - 1));
          return true;
        }
        if (key.downArrow) {
          setSelectedIndex((prev) => Math.min(visible.length - 1, prev + 1));
          return true;
        }
        const sel = visible[selectedIndex];
        if (input === "x" && sel) {
          if (sel.status !== "active") {
            setStatusLine(`${sel.id} is already ${sel.status}`);
            return true;
          }
          setStatusLine(`Stopping ${sel.id}…`);
          onStop(sel.id)
            .then(() => setStatusLine(`Stopped ${sel.id}`))
            .catch((e) =>
              setStatusLine(
                `Stop failed: ${(e as Error).message ?? String(e)}`,
              ),
            );
          return true;
        }
        if (input === "c") {
          setStatusLine("Clearing…");
          onClear()
            .then(() => setStatusLine("Cleared"))
            .catch((e) =>
              setStatusLine(
                `Clear failed: ${(e as Error).message ?? String(e)}`,
              ),
            );
          return true;
        }
        return false;
      },
      { isActive: focused, priority: KeypressPriority.High },
    );

    if (!focused) return null;

    if (ordered.length === 0) {
      return (
        <Box paddingX={1} borderStyle="round" borderColor={Theme.borderAmbient}>
          <Text color={Theme.textChrome}>
            no loops · /loop [interval] &lt;prompt&gt; starts one · esc close
          </Text>
        </Box>
      );
    }

    const nowMs = Date.now();
    const activeCount = ordered.filter((l) => l.status === "active").length;
    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Loops </Text>
          <Text color={Theme.stateActive}>{activeCount}</Text>
          <Text color={Theme.textChrome}> active · {ordered.length} total</Text>
        </Box>
        {visible.map((loop, idx) => (
          <SelectableRow key={loop.id} selected={idx === selectedIndex}>
            <RowText color={STATUS_COLOR[loop.status]}>
              {loop.status === "active" ? "◐" : "·"}{" "}
            </RowText>
            <RowText color={Theme.textChrome}>{loop.id} </RowText>
            <RowText color={Theme.textPrimary}>
              {loop.prompt.replace(/\s+/g, " ").slice(0, 40)}{" "}
            </RowText>
            <RowText color={Theme.textSecondary}>
              {describeSchedule(loop)} · {untilNext(loop, nowMs)} · {loop.fires}{" "}
              fired
            </RowText>
          </SelectableRow>
        ))}
        {ordered.length > visible.length && (
          <Text color={Theme.textChrome}>
            +{ordered.length - visible.length} more
          </Text>
        )}
        <Text color={statusLine ? Theme.stateActive : Theme.textChrome}>
          {statusLine ?? "↑↓ select · x stop · c clear all · esc close"}
        </Text>
      </Box>
    );
  },
);

LoopPanel.displayName = "LoopPanel";
