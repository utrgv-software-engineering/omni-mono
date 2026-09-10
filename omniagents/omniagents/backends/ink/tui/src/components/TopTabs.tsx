import React from "react";
import { Box, Text, useStdout } from "ink";
import {
  useApproval,
  useElicitation,
  useEscalation,
  useMcpApproval,
  useSubagents,
} from "../store/appStore.js";
import { Theme } from "../theme.js";
import type { Screen } from "./StatusBar.js";
import { themedMemo } from "../theme/themed-memo.js";

// Browser-style tabs on the terminal's top row: where you ARE. Solid
// blocks on a filled bar — active tab bright, inactive dim — with the
// decision badge on the chat tab (decisions render there). The bottom
// StatusBar carries what's HAPPENING; each bar has exactly one job.

export const TopTabs = themedMemo<{
  screen: Screen;
  /** `*` = changed since last visited (the browser-unread dot). */
  unread: Record<Screen, boolean>;
  /** The terminal's PTY currently owns the keyboard — so Tab is the
   *  shell's, not ours. The corner hint must say so rather than keep
   *  advertising a key that no longer switches tabs. */
  termCaptured?: boolean;
}>(({ screen, unread, termCaptured = false }) => {
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  const escalation = useEscalation();
  const elicitation = useElicitation();
  const workers = useSubagents();
  const decisionPending =
    !!approval || !!mcpApproval || !!escalation || !!elicitation;
  const runningWorkers = workers.filter((w) => w.status === "running").length;

  const { stdout } = useStdout();
  const width = stdout?.columns ?? 80;

  const star = (id: Screen) => (unread[id] && screen !== id ? "*" : "");
  const labels: Array<{ id: Screen; text: string }> = [
    {
      id: "chat",
      text: decisionPending
        ? ` 1 chat${star("chat")} ⚠ `
        : ` 1 chat${star("chat")} `,
    },
    { id: "review", text: ` 2 review${star("review")} ` },
    {
      id: "agents",
      text:
        runningWorkers > 0
          ? ` 3 agents${star("agents")} ${runningWorkers} `
          : ` 3 agents${star("agents")} `,
    },
    { id: "files", text: ` 4 files${star("files")} ` },
    {
      id: "term",
      text: termCaptured ? ` 5 term ● ` : ` 5 term${star("term")} `,
    },
  ];
  // Captured means Tab belongs to the shell; saying "tab" there would
  // advertise the one key that does not do what the bar implies.
  const hint = termCaptured ? "ctrl+] release " : "tab ";
  const tabsWidth = labels.reduce((sum, l) => sum + l.text.length, 0);
  const filler = " ".repeat(Math.max(0, width - tabsWidth - hint.length));

  return (
    <Box width={width}>
      {labels.map((label) => (
        <Text
          key={label.id}
          backgroundColor={
            screen === label.id ? Theme.barActiveBg : Theme.barBg
          }
          color={screen === label.id ? Theme.barActiveFg : Theme.textSecondary}
          bold={screen === label.id}
        >
          {label.text}
        </Text>
      ))}
      <Text backgroundColor={Theme.barBg}>{filler}</Text>
      <Text backgroundColor={Theme.barBg} color={Theme.textChrome}>
        {hint}
      </Text>
    </Box>
  );
});
