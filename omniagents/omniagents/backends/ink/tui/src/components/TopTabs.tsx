import React from "react";
import { Box, Text } from "ink";
import { useTerminalSize } from "../hooks/useTerminalSize.js";
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
}>(({ screen, unread }) => {
  const approval = useApproval();
  const mcpApproval = useMcpApproval();
  const escalation = useEscalation();
  const elicitation = useElicitation();
  const workers = useSubagents();
  const decisionPending =
    !!approval || !!mcpApproval || !!escalation || !!elicitation;
  const runningWorkers = workers.filter((w) => w.status === "running").length;

  const { columns: width } = useTerminalSize();

  const star = (id: Screen) => (unread[id] && screen !== id ? "*" : "");
  const labels: Array<{ id: Screen; text: string }> = [
    {
      id: "chat",
      text: decisionPending
        ? ` 1 chat${star("chat")} ⚠ `
        : ` 1 chat${star("chat")} `,
    },
    {
      id: "agents",
      text:
        runningWorkers > 0
          ? ` 2 agents${star("agents")} ${runningWorkers} `
          : ` 2 agents${star("agents")} `,
    },
  ];
  const hint = "tab ";
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
