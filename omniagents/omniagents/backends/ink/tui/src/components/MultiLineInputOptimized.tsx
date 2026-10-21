/**
 * Optimized multi-line input with atomic state updates
 * Applies edits synchronously and batches display updates in React
 */

import React, {
  useState,
  useCallback,
  useEffect,
  useLayoutEffect,
} from "react";
import { Box, Text, ResizeObserver, useStdout, type DOMElement } from "ink";
import { layoutInput, columnAtCell } from "./input-layout.js";
import chalk from "chalk";
import { useKeypress } from "../hooks/useKeypress.js";
import { KeypressPriority } from "../contexts/KeypressContext.js";
import {
  detectComposerToken,
  type ComposerCompletion,
  type ComposerToken,
} from "./composer-token.js";
import { Theme } from "../theme.js";
import { themedMemo } from "../theme/themed-memo.js";

interface InputState {
  lines: string[];
  cursorRow: number;
  cursorCol: number;
  scrollOffset: number;
  historyIndex: number;
  draft: string;
}

export interface ComposerDraft {
  id?: number;
  current?: InputState;
  pending?: boolean;
  notify?: (state: InputState) => void;
  release?: () => void;
}

const codePointLength = (value: string) => Array.from(value).length;

const stripAnsi = (value: string) => {
  return value
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b[PX^_].*?\x1b\\/g, "")
    .replace(/\x1b/g, "");
};

type InputAction =
  | { type: "SET_CURSOR"; row: number; col: number }
  | { type: "INSERT_TEXT"; text: string }
  | { type: "INSERT_CHAR"; char: string }
  | { type: "INSERT_NEWLINE" }
  | { type: "DELETE_CHAR" }
  | { type: "DELETE_CHAR_FORWARD" }
  | { type: "MOVE_UP" }
  | { type: "MOVE_DOWN" }
  | { type: "MOVE_LEFT" }
  | { type: "MOVE_RIGHT" }
  | { type: "CLEAR" }
  | { type: "SET_VALUE"; value: string }
  | { type: "SET_HISTORY_INDEX"; index: number; value: string }
  | { type: "UPDATE_HISTORY_INDEX"; index: number }
  | { type: "SAVE_DRAFT"; draft: string }
  | {
      type: "REPLACE_RANGE";
      row: number;
      start: number;
      end: number;
      text: string;
    };

export interface MultiLineInputProps {
  onSubmit: (value: string) => void | boolean | Promise<void | boolean>;
  draft?: ComposerDraft;
  onEscape?: () => void;
  disabled?: boolean;
  placeholder?: string;
  maxHeight?: number;
  history?: string[];
  disableSubmit?: boolean;
  /** External one-shot insertion (e.g. a command completed from the
   * palette). Consumed at the cursor; the owner clears it via
   * onInsertConsumed. */
  pendingInsert?: string | null;
  onInsertConsumed?: () => void;
  /** Reports the `/command` or `@file` token under the cursor (null when
   * there is none) so the owner can show a completion popup. */
  onTokenChange?: (token: ComposerToken | null) => void;
  /** One-shot: replace the completion's token with its text, then
   * optionally submit. Acknowledged via onCompletionConsumed. */
  completion?: ComposerCompletion | null;
  onCompletionConsumed?: () => void;
}

/**
 * Reducer handles ALL state updates atomically
 * Single update = single render (no cursor flicker)
 */
export function inputReducer(
  state: InputState,
  action: InputAction,
): InputState {
  switch (action.type) {
    case "SET_CURSOR":
      return { ...state, cursorRow: action.row, cursorCol: action.col };
    case "INSERT_TEXT": {
      if (!action.text) return state;
      const chunks = action.text.replace(/\r\n?/g, "\n").split("\n");
      const chars = Array.from(state.lines[state.cursorRow] ?? "");
      const before = chars.slice(0, state.cursorCol).join("");
      const after = chars.slice(state.cursorCol).join("");
      const col =
        chunks.length === 1
          ? state.cursorCol + codePointLength(chunks[0])
          : codePointLength(chunks[chunks.length - 1]);
      chunks[0] = before + chunks[0];
      chunks[chunks.length - 1] += after;
      return {
        ...state,
        lines: state.lines
          .slice(0, state.cursorRow)
          .concat(chunks, state.lines.slice(state.cursorRow + 1)),
        cursorRow: state.cursorRow + chunks.length - 1,
        cursorCol: col,
      };
    }

    case "INSERT_CHAR": {
      const newLines = [...state.lines];
      const currentLine = newLines[state.cursorRow] || "";

      const chars = Array.from(currentLine);
      chars.splice(state.cursorCol, 0, action.char);
      newLines[state.cursorRow] = chars.join("");

      return {
        ...state,
        lines: newLines,
        cursorCol: state.cursorCol + 1,
      };
    }

    case "INSERT_NEWLINE": {
      const newLines = [...state.lines];
      const currentLine = newLines[state.cursorRow] || "";
      const chars = Array.from(currentLine);
      const before = chars.slice(0, state.cursorCol).join("");
      const after = chars.slice(state.cursorCol).join("");
      newLines[state.cursorRow] = before;
      newLines.splice(state.cursorRow + 1, 0, after);

      return {
        ...state,
        lines: newLines,
        cursorRow: state.cursorRow + 1,
        cursorCol: 0,
      };
    }

    case "DELETE_CHAR": {
      if (state.cursorCol > 0) {
        const newLines = [...state.lines];
        const currentLine = newLines[state.cursorRow] || "";

        const chars = Array.from(currentLine);
        chars.splice(state.cursorCol - 1, 1);
        newLines[state.cursorRow] = chars.join("");

        return {
          ...state,
          lines: newLines,
          cursorCol: state.cursorCol - 1,
        };
      } else if (state.cursorRow > 0) {
        const newLines = [...state.lines];
        const prevLine = newLines[state.cursorRow - 1] || "";
        const currentLine = newLines[state.cursorRow] || "";
        const newCol = codePointLength(prevLine);
        newLines[state.cursorRow - 1] = prevLine + currentLine;
        newLines.splice(state.cursorRow, 1);

        return {
          ...state,
          lines: newLines,
          cursorRow: state.cursorRow - 1,
          cursorCol: newCol,
        };
      }
      return state;
    }

    case "DELETE_CHAR_FORWARD": {
      const currentLine = state.lines[state.cursorRow] || "";
      const chars = Array.from(currentLine);
      if (state.cursorCol < chars.length) {
        const newLines = [...state.lines];
        chars.splice(state.cursorCol, 1);
        newLines[state.cursorRow] = chars.join("");

        return {
          ...state,
          lines: newLines,
        };
      } else if (state.cursorRow < state.lines.length - 1) {
        const newLines = [...state.lines];
        const nextLine = newLines[state.cursorRow + 1] || "";
        newLines[state.cursorRow] = currentLine + nextLine;
        newLines.splice(state.cursorRow + 1, 1);

        return {
          ...state,
          lines: newLines,
        };
      }
      return state;
    }

    case "MOVE_UP": {
      if (state.cursorRow > 0) {
        const newRow = state.cursorRow - 1;
        const newLine = state.lines[newRow] || "";
        return {
          ...state,
          cursorRow: newRow,
          cursorCol: Math.min(state.cursorCol, codePointLength(newLine)),
        };
      }
      return state;
    }

    case "MOVE_DOWN": {
      if (state.cursorRow < state.lines.length - 1) {
        const newRow = state.cursorRow + 1;
        const newLine = state.lines[newRow] || "";
        return {
          ...state,
          cursorRow: newRow,
          cursorCol: Math.min(state.cursorCol, codePointLength(newLine)),
        };
      }
      return state;
    }

    case "MOVE_LEFT": {
      if (state.cursorCol > 0) {
        return {
          ...state,
          cursorCol: state.cursorCol - 1,
        };
      } else if (state.cursorRow > 0) {
        const prevLine = state.lines[state.cursorRow - 1] || "";
        return {
          ...state,
          cursorRow: state.cursorRow - 1,
          cursorCol: codePointLength(prevLine),
        };
      }
      return state;
    }

    case "MOVE_RIGHT": {
      const currentLine = state.lines[state.cursorRow] || "";
      if (state.cursorCol < codePointLength(currentLine)) {
        return {
          ...state,
          cursorCol: state.cursorCol + 1,
        };
      } else if (state.cursorRow < state.lines.length - 1) {
        return {
          ...state,
          cursorRow: state.cursorRow + 1,
          cursorCol: 0,
        };
      }
      return state;
    }

    case "CLEAR":
      return {
        lines: [""],
        cursorRow: 0,
        cursorCol: 0,
        scrollOffset: 0,
        historyIndex: state.historyIndex,
        draft: "",
      };

    case "SET_VALUE": {
      const newLines = action.value
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n")
        .split("\n");
      return {
        ...state,
        lines: newLines.length > 0 ? newLines : [""],
        cursorRow: newLines.length - 1,
        cursorCol: codePointLength(newLines[newLines.length - 1] ?? ""),
      };
    }

    case "SET_HISTORY_INDEX":
      return {
        ...state,
        historyIndex: action.index,
        lines: action.value
          .replace(/\r\n/g, "\n")
          .replace(/\r/g, "\n")
          .split("\n"),
        cursorRow: 0,
        cursorCol: 0,
      };

    case "UPDATE_HISTORY_INDEX":
      // Update index only, don't touch cursor or content
      return {
        ...state,
        historyIndex: action.index,
      };

    case "SAVE_DRAFT":
      return {
        ...state,
        draft: action.draft,
      };

    case "REPLACE_RANGE": {
      // Code-point columns on one row; the cursor lands after the text.
      const chars = Array.from(state.lines[action.row] ?? "");
      const start = Math.max(0, Math.min(action.start, chars.length));
      const end = Math.max(start, Math.min(action.end, chars.length));
      const newLines = [...state.lines];
      newLines[action.row] =
        chars.slice(0, start).join("") +
        action.text +
        chars.slice(end).join("");
      return {
        ...state,
        lines: newLines,
        cursorRow: action.row,
        cursorCol: start + codePointLength(action.text),
      };
    }

    default:
      return state;
  }
}

export const MultiLineInputOptimized = themedMemo<MultiLineInputProps>(
  ({
    onSubmit,
    onEscape,
    disabled = false,
    placeholder = "Type your message...",
    maxHeight = 20,
    history = [],
    disableSubmit = false,
    pendingInsert = null,
    onInsertConsumed,
    onTokenChange,
    completion = null,
    onCompletionConsumed,
    draft: suppliedDraft,
  }) => {
    const localDraft = React.useRef<ComposerDraft>({});
    const draft = suppliedDraft ?? localDraft.current;
    const [state, setState] = useState<InputState>(
      () =>
        draft.current ?? {
          lines: [""],
          cursorRow: 0,
          cursorCol: 0,
          scrollOffset: 0,
          historyIndex: history.length,
          draft: "",
        },
    );

    const stateRef = draft as ComposerDraft & { current: InputState };
    stateRef.current ??= state;
    useLayoutEffect(() => {
      draft.notify = setState;
      return () => {
        if (draft.notify === setState) draft.notify = undefined;
        draft.release?.();
      };
    }, [draft]);
    // Input events can arrive back-to-back before React commits. Apply each
    // edit once, synchronously, then let React batch the display updates.
    const dispatch = useCallback(
      (action: InputAction) => {
        const next = inputReducer(stateRef.current, action);
        stateRef.current = next;
        draft.notify?.(next);
      },
      [draft],
    );
    const { stdout } = useStdout();
    const element = React.useRef<DOMElement>(null);
    const [measuredWidth, setMeasuredWidth] = useState<number | null>(null);
    const [terminalRows, setTerminalRows] = useState(stdout.rows ?? 24);
    useLayoutEffect(() => {
      const observer = new ResizeObserver((entries) => {
        for (const entry of entries) setMeasuredWidth(entry.contentRect.width);
      });
      if (element.current) observer.observe(element.current);
      const resize = () => setTerminalRows(stdout.rows ?? 24);
      stdout.on("resize", resize);
      return () => {
        observer.disconnect();
        stdout.off("resize", resize);
      };
    }, [stdout]);
    // Border (2), padding (2), and the prompt gutter (2).
    const columns = Math.max(1, (measuredWidth ?? stdout.columns ?? 80) - 6);
    const layout = React.useMemo(
      () => layoutInput(state.lines, state.cursorRow, state.cursorCol, columns),
      [state.lines, state.cursorRow, state.cursorCol, columns],
    );
    const moveVertical = (direction: -1 | 1) => {
      const current = stateRef.current;
      const now = layoutInput(
        current.lines,
        current.cursorRow,
        current.cursorCol,
        columns,
      );
      const next = now.rows[now.cursorRow + direction];
      if (!next) return false;
      dispatch({
        type: "SET_CURSOR",
        row: next.line,
        col: Math.min(
          codePointLength(current.lines[next.line]),
          columnAtCell(next, now.cursorCell),
        ),
      });
      return true;
    };

    // One-shot external insertion (command palette completion): text
    // arrives via props, lands at the cursor, and is acknowledged so
    // the owner clears it — re-renders can't double-insert.
    useEffect(() => {
      if (pendingInsert) {
        dispatch({ type: "INSERT_TEXT", text: pendingInsert });
        onInsertConsumed?.();
      }
    }, [pendingInsert]);
    // Update history index when history changes
    // But DON'T reset cursor/content - that's only for arrow key navigation
    const prevHistoryLengthRef = React.useRef(history.length);

    useEffect(() => {
      // Only update historyIndex, don't touch lines or cursor
      // This prevents cursor reset when new messages are added
      const prev = prevHistoryLengthRef.current;
      prevHistoryLengthRef.current = history.length;

      // When history grows (new message submitted), just update the index
      // Don't reset cursor position or content!
      if (history.length > prev) {
        dispatch({ type: "UPDATE_HISTORY_INDEX", index: history.length });
      }
    }, [history.length]);

    const getValue = () => stateRef.current.lines.join("\n");

    const insertText = useCallback(
      (text: string) => {
        if (!text) {
          return;
        }

        const current = stateRef.current;
        if (current.historyIndex !== history.length) {
          dispatch({ type: "UPDATE_HISTORY_INDEX", index: history.length });
        }

        dispatch({ type: "INSERT_TEXT", text });
      },
      [history.length],
    );

    const handleSubmit = useCallback(() => {
      if (draft.pending) return;
      const value = getValue().trim();
      if (value) {
        const submitted = stateRef.current;
        dispatch({ type: "CLEAR" });
        const restore = () => {
          const current = stateRef.current;
          const next =
            current.lines.join("\n") === ""
              ? submitted
              : {
                  ...current,
                  lines: [...submitted.lines, ...current.lines],
                  cursorRow: submitted.lines.length + current.cursorRow,
                };
          stateRef.current = next;
          draft.notify?.(next);
        };
        draft.pending = true;
        try {
          const result = onSubmit(value);
          if (result && typeof result === "object" && "then" in result) {
            void result
              .then((ok) => {
                if (ok === false) restore();
              }, restore)
              .finally(() => {
                draft.pending = false;
                draft.release?.();
              });
          } else {
            if (result === false) restore();
            draft.pending = false;
          }
        } catch {
          restore();
          draft.pending = false;
        }
      }
    }, [onSubmit, getValue, draft]);

    // Tell the owner what sits under the cursor so a popup can offer
    // completions. Fired from committed state, not from every keystroke,
    // so a paste burst reports once.
    useEffect(() => {
      onTokenChange?.(
        disabled
          ? null
          : detectComposerToken(state.lines, state.cursorRow, state.cursorCol),
      );
    }, [
      state.lines,
      state.cursorRow,
      state.cursorCol,
      disabled,
      onTokenChange,
    ]);

    // One-shot completion from the popup: replace the token, acknowledge,
    // and send if the popup asked for it (a command with no arguments).
    // dispatch updates stateRef synchronously, so the submit sees the
    // replaced text.
    useEffect(() => {
      if (!completion) return;
      const { token, text, submit } = completion;
      dispatch({
        type: "REPLACE_RANGE",
        row: token.row,
        start: token.start,
        end: token.end,
        text,
      });
      onCompletionConsumed?.();
      if (submit && !disableSubmit) handleSubmit();
    }, [completion]);

    const handleHistoryUp = useCallback(() => {
      if (history.length === 0) return;

      const current = stateRef.current;

      if (current.historyIndex === history.length) {
        dispatch({
          type: "SAVE_DRAFT",
          draft: current.lines.join("\n"),
        });
      }

      if (current.historyIndex > 0) {
        const newIndex = current.historyIndex - 1;
        dispatch({
          type: "SET_HISTORY_INDEX",
          index: newIndex,
          value: history[newIndex],
        });
      }
    }, [history]);

    const handleHistoryDown = useCallback(() => {
      if (history.length === 0) return;

      const current = stateRef.current;

      if (current.historyIndex < history.length - 1) {
        const newIndex = current.historyIndex + 1;
        dispatch({
          type: "SET_HISTORY_INDEX",
          index: newIndex,
          value: history[newIndex],
        });
      } else if (current.historyIndex === history.length - 1) {
        dispatch({
          type: "SET_HISTORY_INDEX",
          index: history.length,
          value: current.draft,
        });
      }
    }, [history]);

    useKeypress(
      (input, key) => {
        const current = stateRef.current;
        if (key.paste) {
          insertText(
            stripAnsi(input)
              .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "")
              .replace(/\t/g, "    "),
          );
          return true;
        }

        // Tab belongs to the global screen switcher — never a literal
        // character in the composer.
        if (key.tab) {
          return false;
        }

        if (key.escape) {
          onEscape?.();
          return;
        }

        if (key.return) {
          if (key.meta || key.ctrl || key.shift) {
            dispatch({ type: "INSERT_NEWLINE" });
          } else {
            if (!disableSubmit) {
              handleSubmit();
            }
          }
        } else if (key.backspace) {
          dispatch({ type: "DELETE_CHAR" });
        } else if (key.delete) {
          dispatch({ type: "DELETE_CHAR_FORWARD" });
        } else if (key.ctrl && input === "d") {
          dispatch({ type: "DELETE_CHAR_FORWARD" });
        } else if (key.home || key.end) {
          dispatch({
            type: "SET_CURSOR",
            row: current.cursorRow,
            col: key.home
              ? 0
              : codePointLength(current.lines[current.cursorRow]),
          });
        } else if (key.upArrow) {
          if (!moveVertical(-1) && !key.shift) handleHistoryUp();
        } else if (key.downArrow) {
          if (!moveVertical(1) && !key.shift) handleHistoryDown();
        } else if (key.leftArrow) {
          dispatch({ type: "MOVE_LEFT" });
        } else if (key.rightArrow) {
          dispatch({ type: "MOVE_RIGHT" });
        } else if (input && !key.ctrl && !key.meta) {
          if (input === "\x1b[200~" || input === "[200~") {
            return;
          }

          if (input === "\x1b[201~" || input === "[201~") {
            return;
          }

          const normalized = input
            .replace(/\x1b\[200~/g, "")
            .replace(/\x1b\[201~/g, "")
            .replace(/\[200~/g, "")
            .replace(/\[201~/g, "")
            .replace(/\r\n/g, "\n")
            .replace(/\r/g, "\n");

          if (normalized === "\x7f" || normalized === "\b") {
            dispatch({ type: "DELETE_CHAR" });
            return;
          }

          const withoutMouse = normalized
            .replace(/(?:\x1b)?\[<\d+;\d+;\d+[Mm]/g, "")
            .replace(/(?:\x1b)?<\d+;\d+;\d+[Mm]/g, "")
            .replace(/(?:\x1b)?\[M[\s\S]{3}/g, "");

          if (!withoutMouse) {
            return;
          }

          const clean = stripAnsi(withoutMouse)
            .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "")
            .replace(/\t/g, "    ");
          if (clean) {
            insertText(clean);
          }
        } else {
          return false;
        }
        return true;
      },
      { isActive: !disabled, priority: KeypressPriority.Normal },
    );

    const visibleHeight = Math.max(
      1,
      Math.min(maxHeight, terminalRows - 8, layout.rows.length),
    );
    const scrollRef = React.useRef(0);
    const scrollOffset = Math.max(
      0,
      Math.min(
        layout.cursorRow < scrollRef.current
          ? layout.cursorRow
          : Math.max(scrollRef.current, layout.cursorRow - visibleHeight + 1),
        layout.rows.length - visibleHeight,
      ),
    );
    useLayoutEffect(() => {
      scrollRef.current = scrollOffset;
    }, [scrollOffset]);
    const visibleLines = layout.rows.slice(
      scrollOffset,
      scrollOffset + visibleHeight,
    );

    return (
      <Box
        ref={element}
        borderStyle="single"
        borderColor={Theme.borderAmbient}
        paddingX={1}
        flexDirection="column"
        minHeight={1}
        width="100%"
      >
        {visibleLines.map((row, visibleIdx) => {
          const line = row.text;
          const actualIdx = scrollOffset + visibleIdx;
          const isCurrentRow = actualIdx === layout.cursorRow;
          const showPrompt = actualIdx === 0;
          const localCol = state.cursorCol - row.start;

          const prompt = showPrompt ? "> " : "  ";
          const textColor = chalk.hex(Theme.textPrimary);

          let lineDisplay: string;
          if (isCurrentRow) {
            const chars = Array.from(line);
            const beforeRaw = chars.slice(0, localCol).join("");
            const cursorRaw = chars[localCol];
            const afterRaw =
              cursorRaw === undefined ? "" : chars.slice(localCol + 1).join("");
            const beforeColored = beforeRaw ? textColor(beforeRaw) : "";
            const cursorColored = chalk.inverse(cursorRaw ?? " ");
            const afterColored = afterRaw ? textColor(afterRaw) : "";
            lineDisplay = `${beforeColored}${cursorColored}${afterColored}`;
          } else {
            const showPlaceholder = state.lines.every((l) => l === "");
            const displayValue =
              line ||
              (showPlaceholder && actualIdx === 0 && !line ? placeholder : "");
            lineDisplay = displayValue ? textColor(displayValue) : "";
          }

          return (
            <Box key={actualIdx}>
              <Text color={Theme.accentUser}>{prompt}</Text>
              <Text wrap="truncate">{lineDisplay}</Text>
            </Box>
          );
        })}
      </Box>
    );
  },
  (prevProps, nextProps) => {
    // Re-render if any props that are used in the useInput closure change
    // This prevents stale closures when callbacks are recreated with new dependencies
    return (
      prevProps.history === nextProps.history &&
      prevProps.draft === nextProps.draft &&
      prevProps.placeholder === nextProps.placeholder &&
      prevProps.maxHeight === nextProps.maxHeight &&
      prevProps.disabled === nextProps.disabled &&
      prevProps.disableSubmit === nextProps.disableSubmit &&
      prevProps.onSubmit === nextProps.onSubmit &&
      prevProps.onEscape === nextProps.onEscape &&
      prevProps.pendingInsert === nextProps.pendingInsert &&
      prevProps.onInsertConsumed === nextProps.onInsertConsumed &&
      prevProps.onTokenChange === nextProps.onTokenChange &&
      prevProps.completion === nextProps.completion &&
      prevProps.onCompletionConsumed === nextProps.onCompletionConsumed
    );
  },
);
