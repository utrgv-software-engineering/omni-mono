import React from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import {
  useAppStore,
  useBashJobs,
  useGoal,
  useLoops,
  useArtifacts,
  useNotifications,
  useQueuedMessages,
  useRunDiff,
  useTasks,
  useWakeup,
  useSubagents,
} from "../store/appStore.js";
import { buildPanelMenuRows, type MenuPanelId } from "./strip-model.js";
import { Theme } from "../theme.js";

type Props = {
  open: boolean;
  onSelect: (panel: MenuPanelId) => void;
  onClose: () => void;
};

// Which-key panel menu (ctrl+a): a transient popup where every panel is
// one mnemonic keypress away, listed with its live summary. The menu IS
// the documentation — the direct chords (ctrl+b/n/g, /model) are
// accelerators you graduate into. Panels needing attention sort first,
// so "show me the problem" is always the first key listed.
export const PanelMenu = React.memo(({ open, onSelect, onClose }: Props) => {
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
  const dismissedJobIds = useAppStore((s) => s.dismissedJobIds);
  const dismissedSubagentIds = useAppStore((s) => s.dismissedSubagentIds);

  const rows = buildPanelMenuRows({
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

  useKeypress(
    (input, key) => {
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      // Esc only — every letter is a live mnemonic (q is the queue).
      if (key.escape) {
        onClose();
        return true;
      }
      const row = rows.find((r) => r.key === input);
      if (row) {
        onSelect(row.panel);
        return true;
      }
      return true; // modal — swallow the rest
    },
    { isActive: open, priority: KeypressPriority.Critical },
  );

  if (!open) return null;

  return (
    <Box
      flexDirection="column"
      paddingX={1}
      borderStyle="round"
      borderColor={Theme.stateActive}
    >
      <Text color={Theme.textChrome}>panels</Text>
      {rows.map((row) => (
        <Box key={row.panel}>
          <Text color={Theme.stateActive} bold>
            {"  "}
            {row.key}
          </Text>
          <Text color={Theme.textPrimary}>
            {"  "}
            {row.label}
          </Text>
          {row.summary && (
            <Text
              color={
                row.warn ? Theme.stateBlocked : Theme.textChrome
              }
            >
              {"  "}
              {row.summary}
            </Text>
          )}
        </Box>
      ))}
      <Text color={Theme.textChrome}>press key · esc close</Text>
    </Box>
  );
});
