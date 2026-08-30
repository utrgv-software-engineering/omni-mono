import React, { useCallback, useEffect, useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import { useQueuedMessages } from "../store/appStore.js";
import { windowStart } from "./model-picker-model.js";
import { SelectableRow } from "./shared/SelectableRow.js";
import { Theme } from "../theme.js";

const MAX_VISIBLE = 5;

export type QueueCancelResult = {
  ok: boolean;
  reason?: string;
};

type Props = {
  focused: boolean;
  onUnfocus: () => void;
  /**
   * Remove one item from the server-side queue. Resolves with the
   * server's verdict — an item the drainer already popped reports
   * `{ok: false, reason: "not_found"}` rather than throwing, and the
   * panel says so instead of pretending the cancel landed.
   */
  onCancel: (itemId: string) => Promise<QueueCancelResult>;
};

/**
 * "Up next" dock rendered just above the prompt: user messages enqueued
 * while a run is in flight, in drain order.
 *
 * The queue is the one dock surface whose items the user can still take
 * back — a message queued behind a long run has not been sent to the
 * model yet — so this panel owns a cursor and a cancel key. Cancelling
 * the item currently *running* is not possible here on purpose: that is
 * `stop_run`'s job (Esc on the chat screen), and offering one gesture
 * that silently means two different operations is how a user stops
 * trusting either.
 */
export const QueuedMessages = React.memo<Props>(
  ({ focused, onUnfocus, onCancel }) => {
    const items = useQueuedMessages();
    const [index, setIndex] = useState(0);
    const [statusLine, setStatusLine] = useState<string | null>(null);
    const [cancelling, setCancelling] = useState(false);

    // The drainer mutates this list underneath the cursor; clamp rather
    // than let the selection point past the end.
    useEffect(() => {
      setIndex((prev) =>
        items.length === 0 ? 0 : Math.min(prev, items.length - 1),
      );
    }, [items.length]);

    // A verdict about an item that is no longer listed is noise.
    useEffect(() => {
      if (!focused) setStatusLine(null);
    }, [focused]);

    const cancel = useCallback(async () => {
      const item = items[index];
      if (!item || cancelling) return;
      setCancelling(true);
      setStatusLine("cancelling…");
      try {
        const result = await onCancel(item.id);
        setStatusLine(
          result?.ok
            ? "cancelled"
            : `could not cancel: ${result?.reason ?? "already running"}`,
        );
      } catch (e) {
        setStatusLine(`cancel failed: ${(e as Error).message ?? String(e)}`);
      } finally {
        setCancelling(false);
      }
    }, [items, index, onCancel, cancelling]);

    useKeypress(
      (input, key) => {
        // Chords and Tab belong to the global layer — this drawer must
        // never swallow Ctrl+C or screen switching.
        if (key.ctrl || (key.meta && !key.escape)) return false;
        if (key.tab) return false;
        if (key.escape) {
          onUnfocus();
          return true;
        }
        if (items.length === 0) return true;
        if (key.upArrow || input === "k") {
          setIndex((prev) => (prev > 0 ? prev - 1 : prev));
          return true;
        }
        if (key.downArrow || input === "j") {
          setIndex((prev) => (prev < items.length - 1 ? prev + 1 : prev));
          return true;
        }
        if (input === "d" || input === "x") {
          void cancel();
          return true;
        }
        return true; // focused drawer — swallow the rest
      },
      { isActive: focused, priority: KeypressPriority.High },
    );

    if (!focused) return null;

    if (!items || items.length === 0) {
      return (
        <Box
          flexDirection="column"
          paddingX={1}
          borderStyle="round"
          borderColor={Theme.borderAmbient}
        >
          <Text color={Theme.textChrome}>queue is empty</Text>
          {statusLine && (
            <Text color={Theme.stateBlocked} italic>
              {statusLine}
            </Text>
          )}
          <Text color={Theme.textChrome}>esc close</Text>
        </Box>
      );
    }

    // The window follows the cursor. Slicing a fixed head instead would
    // let the selection walk off the bottom of a long queue — invisible,
    // but still what `d` cancels.
    const start = windowStart(index, items.length, MAX_VISIBLE);
    const visible = items.slice(start, start + MAX_VISIBLE);
    const hiddenAbove = start;
    const hiddenBelow = items.length - (start + visible.length);

    return (
      <Box
        flexDirection="column"
        paddingX={1}
        borderStyle="round"
        borderColor={Theme.borderFocus}
      >
        <Box>
          <Text color={Theme.textChrome}>Up next </Text>
          <Text color={Theme.stateActive}>{items.length}</Text>
          <Text color={Theme.textChrome}> queued</Text>
        </Box>
        {hiddenAbove > 0 && (
          <Text color={Theme.textChrome}>… {hiddenAbove} earlier</Text>
        )}
        {visible.map((item, idx) => {
          const position = start + idx;
          const isSelected = position === index;
          return (
            <SelectableRow key={item.id} selected={isSelected}>
              <Text color={Theme.textChrome}>{position + 1}. </Text>
              <Text color={Theme.textPrimary} bold={isSelected}>
                {item.content.length > 80
                  ? `${item.content.slice(0, 77)}…`
                  : item.content}
              </Text>
            </SelectableRow>
          );
        })}
        {hiddenBelow > 0 && (
          <Text color={Theme.textChrome}>… +{hiddenBelow} more</Text>
        )}
        {statusLine && (
          <Text color={Theme.stateBlocked} italic>
            {statusLine}
          </Text>
        )}
        <Text color={Theme.textChrome}>↑↓ select · d cancel · esc close</Text>
      </Box>
    );
  },
);

QueuedMessages.displayName = "QueuedMessages";
