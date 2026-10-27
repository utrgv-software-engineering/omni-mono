import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { useGoal } from "../store/appStore.js";
import type { GoalSnapshot } from "@omniagents/client/session/panel-types";
import { Theme, roleMap } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

export type GoalAction = "stop" | "pause" | "resume";

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  onAction: (action: GoalAction) => Promise<{ message?: unknown } | unknown>;
};

const STATUS_COLOR: Record<GoalSnapshot["status"], string> = roleMap({
  active: "stateActive",
  paused: "stateBlocked",
  completed: "stateDone",
  cancelled: "stateFailed",
});

/** Keys the panel offers for the goal's current state. */
export function goalActions(goal: GoalSnapshot | null): GoalAction[] {
  if (!goal) return [];
  if (goal.status === "active") return ["pause", "stop"];
  if (goal.status === "paused") return ["resume", "stop"];
  return [];
}

function elapsed(startedAt: number, nowMs: number): string {
  const seconds = Math.max(0, Math.floor(nowMs / 1000 - startedAt));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h${(minutes % 60).toString().padStart(2, "0")}`;
}

/**
 * The goal drawer: what `/goal` with nothing after it opens. Shows the
 * running goal and offers its actions as keys, so "stop the goal" is a
 * keypress in the place the goal is shown rather than a second word to
 * remember. `/goal <text>` still starts one from the composer.
 */
export const GoalPanel = themedMemo(
  ({ focused, onUnfocus, onAction }: Props) => {
    const goal = useGoal();
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [, setTick] = useState(0);
    const actions = goalActions(goal);

    useEffect(() => {
      if (!focused || !goal || goal.status !== "active") return;
      const id = setInterval(() => setTick((n) => n + 1), 1000);
      return () => clearInterval(id);
    }, [focused, goal]);
    useEffect(() => {
      if (!statusLine) return;
      const id = setTimeout(() => setStatusLine(null), 2500);
      return () => clearTimeout(id);
    }, [statusLine]);

    const run = (action: GoalAction) => {
      setStatusLine(`${action}…`);
      onAction(action)
        .then((res) => {
          const message = (res as { message?: unknown } | undefined)?.message;
          setStatusLine(
            typeof message === "string" ? message : `${action}: ok`,
          );
        })
        .catch((e) =>
          setStatusLine(
            `${action} failed: ${(e as Error).message ?? String(e)}`,
          ),
        );
    };

    useKeypress(
      (input, key) => {
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false;
        if (key.escape) {
          onUnfocus();
          return true;
        }
        if (input === "s" && actions.includes("stop")) {
          run("stop");
          return true;
        }
        if (input === "p" && actions.includes("pause")) {
          run("pause");
          return true;
        }
        if (input === "r" && actions.includes("resume")) {
          run("resume");
          return true;
        }
        return false;
      },
      { isActive: focused, priority: KeypressPriority.High },
    );

    if (!focused) return null;

    if (!goal) {
      return (
        <Box paddingX={1} borderStyle="round" borderColor={Theme.borderAmbient}>
          <Text color={Theme.textChrome}>
            no goal · /goal &lt;text&gt; starts one · esc close
          </Text>
        </Box>
      );
    }

    const hints = [
      actions.includes("pause") ? "p pause" : null,
      actions.includes("resume") ? "r resume" : null,
      actions.includes("stop") ? "s stop" : null,
      "esc close",
    ]
      .filter((h): h is string => h !== null)
      .join(" · ");

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Goal </Text>
          <Text color={STATUS_COLOR[goal.status]}>{goal.status}</Text>
          <Text color={Theme.textChrome}>
            {" "}
            · turn {goal.turn}/{goal.max_turns} · every {goal.tick_interval}s ·{" "}
            {elapsed(goal.started_at, Date.now())}
          </Text>
        </Box>
        <Text color={Theme.textPrimary} wrap="truncate-end">
          {goal.goal}
        </Text>
        {goal.last_reason && (
          <Text color={Theme.textSecondary} wrap="truncate-end">
            last: {goal.last_reason}
          </Text>
        )}
        {goal.completion_reason && (
          <Text color={Theme.textSecondary} wrap="truncate-end">
            ended: {goal.completion_reason}
            {goal.evidence ? ` — ${goal.evidence}` : ""}
          </Text>
        )}
        <Text color={statusLine ? Theme.stateActive : Theme.textChrome}>
          {statusLine ?? hints}
        </Text>
      </Box>
    );
  },
);

GoalPanel.displayName = "GoalPanel";
