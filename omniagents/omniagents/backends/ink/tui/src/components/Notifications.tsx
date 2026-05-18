import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import {
  useActions,
  useNotifications,
} from "../store/appStore.js";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { TokyoNightTheme } from "../theme.js";

const MAX_VISIBLE = 5;
const MESSAGE_TRUNCATE_UNFOCUSED = 70;
const MESSAGE_TRUNCATE_FOCUSED = 80;

function shortMessage(message: string, max: number): string {
  const oneLine = message.replace(/\s+/g, " ").trim();
  if (oneLine.length <= max) return oneLine;
  return oneLine.slice(0, max - 1) + "…";
}

type Props = {
  focused: boolean;
  onUnfocus: () => void;
};

// Docked notifications panel. Accumulates ``notify`` calls from the
// agent and persists them until the user explicitly dismisses them —
// long-running runs can drop many heads-ups; the user catches up at a
// glance and decides what to clear.
//
// Two states:
// - Unfocused: collapsed single line ("N notifications · Ctrl+N to manage").
// - Focused (Ctrl+N): full list with cursor; ↑↓ navigate, d dismiss,
//   D dismiss all, esc exit.
export const Notifications = React.memo(({ focused, onUnfocus }: Props) => {
  const notifications = useNotifications();
  const actions = useActions();

  // Reverse-chronological — newest first so latest heads-up is at the top.
  const ordered = [...notifications].reverse();
  const visible = ordered.slice(0, MAX_VISIBLE);
  const overflow = ordered.length - visible.length;

  const [selectedIndex, setSelectedIndex] = useState(0);
  useEffect(() => {
    setSelectedIndex((prev) => {
      if (visible.length === 0) return 0;
      return Math.min(prev, visible.length - 1);
    });
  }, [visible.length]);

  useKeypress(
    (input, key) => {
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
      if (input === "D") {
        actions.clearNotifications();
        return true;
      }
      if (input === "d") {
        const sel = visible[selectedIndex];
        if (sel) actions.removeNotification(sel.id);
        return true;
      }
      return false;
    },
    { isActive: focused, priority: KeypressPriority.High },
  );

  if (notifications.length === 0) return null;

  const noun = notifications.length === 1 ? "notification" : "notifications";

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={focused ? TokyoNightTheme.info : TokyoNightTheme.toolResult}
    >
      <Box>
        <Text color={TokyoNightTheme.defaultText}>Notifications  </Text>
        <Text color={TokyoNightTheme.info}>{notifications.length}</Text>
        <Text color={TokyoNightTheme.defaultText} dimColor>
          {" "}{noun}
        </Text>
        {!focused && (
          <Text color={TokyoNightTheme.help}>   Ctrl+N to manage</Text>
        )}
      </Box>

      {visible.map((n, idx) => {
        const isSelected = focused && idx === selectedIndex;
        const max = focused
          ? MESSAGE_TRUNCATE_FOCUSED
          : MESSAGE_TRUNCATE_UNFOCUSED;
        return (
          <Box key={n.id}>
            <Text color={TokyoNightTheme.info}>
              {focused ? (isSelected ? "▶ " : "  ") : "· "}
            </Text>
            <Text color={TokyoNightTheme.defaultText}>
              {shortMessage(n.message, max)}
            </Text>
          </Box>
        );
      })}

      {overflow > 0 && (
        <Text color={TokyoNightTheme.defaultText} dimColor>
          … +{overflow} more
        </Text>
      )}

      {focused && (
        <Box>
          <Text color={TokyoNightTheme.help}>
            ↑↓ select · d dismiss · D dismiss all · esc exit
          </Text>
        </Box>
      )}
    </Box>
  );
});
