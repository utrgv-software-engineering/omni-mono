/**
 * Selection list used by approval dialogs. Renders a column of options with
 * the current selection highlighted, and (optionally) wires keyboard
 * navigation (↑↓, Enter, numeric shortcuts).
 *
 * Inspired by gemini-cli's BaseSelectionList. Kept intentionally narrow:
 * dialogs that need custom keybindings can opt out via `manageInput={false}`
 * and drive the selection externally.
 */

import React, { useState } from "react";
import { Box, Text } from "ink";
import { useKeypress } from "../../hooks/useKeypress.js";
import { KeypressPriority } from "../../contexts/KeypressContext.js";
import { Theme } from "../../theme.js";

export interface SelectionOption<V extends string> {
  title: string;
  description: string;
  value: V;
}

export interface BaseSelectionListProps<V extends string> {
  options: ReadonlyArray<SelectionOption<V>>;
  onSelect: (value: V) => void;
  /**
   * When true (default), the component owns keyboard input for ↑↓/Enter/digits.
   * Set false if the parent already routes input (e.g. via useKeypress).
   */
  manageInput?: boolean;
  /** Controlled selection index. Only honored when `manageInput` is false. */
  selectedIndex?: number;
}

export function BaseSelectionList<V extends string>({
  options,
  onSelect,
  manageInput = true,
  selectedIndex: controlledIndex,
}: BaseSelectionListProps<V>) {
  const [uncontrolledIndex, setUncontrolledIndex] = useState(0);
  const selectedIndex = manageInput
    ? uncontrolledIndex
    : (controlledIndex ?? 0);

  useKeypress(
    (input, key) => {
      // Modified keys are chords for the global layer (quit, panel
      // switching) — never mistake Ctrl+X for the letter x.
      if (key.ctrl || (key.meta && !key.escape)) return false;
      if (key.tab) return false; // tab always cycles screens
      if (key.shift && (key.upArrow || key.downArrow)) return false;
      if (key.upArrow) {
        setUncontrolledIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return true;
      }
      if (key.downArrow) {
        setUncontrolledIndex((prev) =>
          prev < options.length - 1 ? prev + 1 : prev,
        );
        return true;
      }
      if (key.return) {
        onSelect(options[selectedIndex].value);
        return true;
      }
      // Numeric shortcuts: "1" picks options[0], etc.
      const n = parseInt(input, 10);
      if (!Number.isNaN(n) && n >= 1 && n <= options.length) {
        onSelect(options[n - 1].value);
        return true;
      }
      return false;
    },
    { isActive: manageInput, priority: KeypressPriority.Critical },
  );

  return (
    <Box flexDirection="column">
      {options.map((option, index) => (
        <Box key={option.value}>
          {index === selectedIndex ? (
            <Text bold color={Theme.focus}>
              ▶ {option.title} - {option.description}
            </Text>
          ) : (
            <Text color={Theme.textChrome}>
              {option.title} - {option.description}
            </Text>
          )}
        </Box>
      ))}
    </Box>
  );
}
