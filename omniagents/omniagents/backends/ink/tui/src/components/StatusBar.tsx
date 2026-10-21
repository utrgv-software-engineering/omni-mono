import React from "react";
import { Box, Text, useStdout } from "ink";
import {
  useAppStore,
  useBashJobs,
  useGoal,
  useLoops,
  useArtifacts,
  useNotifications,
  useQueuedMessages,
  useRunDiff,
  useSessionModel,
  useStatusContext,
  useTasks,
  useUsage,
  useWakeup,
  useSubagents,
} from "../store/appStore.js";
import { buildStripTokens, type StripTone } from "./strip-model.js";
import { Theme, roleMap } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

export type Screen = "chat" | "agents";

// The status bar: ONE background-filled row at the very bottom of the
// screen, tmux-style — what's HAPPENING. Mission tokens on the left
// (truncated token-by-token, never mid-word), usage pinned right, and
// the armed Ctrl+C warning takes the row over. Screen identity lives in
// TopTabs at the top of the terminal; each bar has exactly one job.
const TONE_FG: Record<StripTone, string> = roleMap({
  info: "stateActive",
  warn: "stateBlocked",
  dim: "textSecondary",
  success: "stateDone",
});

export const StatusBar = themedMemo<{
  waitingForSecondCtrlC: boolean;
}>(({ waitingForSecondCtrlC }) => {
  const { stdout } = useStdout();
  const width = stdout?.columns ?? 80;

  const tasks = useTasks();
  const workers = useSubagents();
  const bashJobs = useBashJobs();
  const queuedMessages = useQueuedMessages();
  const notifications = useNotifications();
  const artifacts = useArtifacts();
  const goal = useGoal();
  const wakeup = useWakeup();
  const loops = useLoops();
  const runDiff = useRunDiff();
  const usage = useUsage();
  const sessionModel = useSessionModel();
  const statusContext = useStatusContext();
  const dismissedJobIds = useAppStore((s) => s.dismissedJobIds);
  const dismissedSubagentIds = useAppStore((s) => s.dismissedSubagentIds);

  const tokens = buildStripTokens({
    tasks,
    subagents: workers.filter(
      (w) => w.status === "running" || !dismissedSubagentIds.has(w.subagent_id),
    ),
    bashJobs: bashJobs.filter(
      (j) => j.running || !dismissedJobIds.has(j.job_id),
    ),
    queuedMessages,
    notificationCount: notifications.length,
    artifactCount: artifacts.length,
    goal,
    wakeup,
    loops,
    runDiff,
  });

  // ----- fixed-width segments -----

  const usageText = (() => {
    const parts: string[] = [];
    if (usage) parts.push(`${Math.round(usage.totalTokens / 1000)}k`);
    if (sessionModel) parts.push(sessionModel);
    return parts.length > 0 ? ` ${parts.join("·")} ` : "";
  })();

  // Middle budget: whatever the tabs and usage leave over. Tokens are
  // dropped whole from the right — never cut mid-token — and the
  // spinner costs two cells when the agent is thinking.
  const contextText = statusContext ? ` ${statusContext} ` : "";
  let middleBudget = Math.max(
    0,
    width - usageText.length - contextText.length - 1,
  );

  const middleTokens: Array<{ text: string; tone: StripTone }> = [];
  if (!waitingForSecondCtrlC) {
    let used = 1;
    for (const token of tokens) {
      const cost = token.text.length + (middleTokens.length > 0 ? 3 : 0);
      if (used + cost > middleBudget) break;
      middleTokens.push(token);
      used += cost;
    }
    middleBudget -= used;
  } else {
    middleBudget -= " ctrl+c again to quit".length;
  }

  const filler = " ".repeat(Math.max(0, middleBudget));

  return (
    <Box width={width}>
      {contextText && (
        <Text backgroundColor={Theme.barBg} color={Theme.textPrimary} bold>
          {contextText}
        </Text>
      )}
      {waitingForSecondCtrlC ? (
        <Text backgroundColor={Theme.barBg} color={Theme.stateBlocked} bold>
          {" ctrl+c again to quit"}
        </Text>
      ) : (
        <>
          <Text backgroundColor={Theme.barBg}> </Text>
          {middleTokens.map((token, index) => (
            <React.Fragment key={`${token.text}-${index}`}>
              {index > 0 && (
                <Text backgroundColor={Theme.barBg} color={Theme.textChrome}>
                  {" · "}
                </Text>
              )}
              <Text backgroundColor={Theme.barBg} color={TONE_FG[token.tone]}>
                {token.text}
              </Text>
            </React.Fragment>
          ))}
        </>
      )}
      <Text backgroundColor={Theme.barBg}>{filler}</Text>
      <Text backgroundColor={Theme.barBg} color={Theme.textSecondary}>
        {usageText}
      </Text>
    </Box>
  );
});
