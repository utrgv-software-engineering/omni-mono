import React, { useEffect, useState } from "react";
import { Box, Text } from "ink";
import { useNotifications } from "../store/appStore.js";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { RowText, SelectableRow } from "./shared/SelectableRow.js";
import { Clip, oneLine } from "./shared/drawer-layout.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

const MAX_VISIBLE = 5;

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  /** Dismiss one notification, or every one when `id` is omitted. */
  onDismiss: (id?: string) => void;
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
export const Notifications = themedMemo(
  ({ focused, onUnfocus, onDismiss }: Props) => {
    const notifications = useNotifications();

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
        if (input === "D") {
          onDismiss();
          return true;
        }
        if (input === "d") {
          const sel = visible[selectedIndex];
          if (sel) onDismiss(sel.id);
          return true;
        }
        return false;
      },
      { isActive: focused, priority: KeypressPriority.High },
    );

    // Drawer model: ambient presence is the ActivityStrip's "N notes"
    // token; the body renders only while focused.
    if (!focused) return null;
    if (notifications.length === 0) {
      return (
        <Box paddingX={1} borderStyle="round" borderColor={Theme.borderAmbient}>
          <Text color={Theme.textChrome}>no notifications</Text>
        </Box>
      );
    }

    const noun = notifications.length === 1 ? "notification" : "notifications";

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.stateActive}
      >
        <Box>
          <Text color={Theme.textChrome}>Notifications </Text>
          <Text color={Theme.stateActive}>{notifications.length}</Text>
          <Text color={Theme.textChrome} dimColor>
            {" "}
            {noun}
          </Text>
        </Box>

        {visible.map((n, idx) => {
          const isSelected = focused && idx === selectedIndex;
          return (
            <SelectableRow key={n.id} selected={isSelected}>
              <Clip>
                <RowText color={Theme.textChrome} wrap="truncate-end">
                  {oneLine(n.message)}
                </RowText>
              </Clip>
            </SelectableRow>
          );
        })}

        {overflow > 0 && (
          <Text color={Theme.textChrome} dimColor>
            … +{overflow} more
          </Text>
        )}

        {focused && (
          <Box>
            <Text color={Theme.textChrome}>
              ↑↓ select · d dismiss · D dismiss all · esc exit
            </Text>
          </Box>
        )}
      </Box>
    );
  },
);
