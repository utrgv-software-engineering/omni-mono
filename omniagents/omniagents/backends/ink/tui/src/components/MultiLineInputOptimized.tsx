/**
 * Optimized multi-line input with atomic state updates
 * Uses useReducer to batch cursor + content updates in single render
 */

import React, { useReducer, useCallback, useEffect, useLayoutEffect } from "react";
import { Box, Text, useInput } from "ink";
import chalk from "chalk";
import { TokyoNightTheme } from "../theme.js";

interface InputState {
  lines: string[];
  cursorRow: number;
  cursorCol: number;
  scrollOffset: number;
  historyIndex: number;
  draft: string;
}

type InputAction =
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
  | { type: "SAVE_DRAFT"; draft: string };

interface MultiLineInputProps {
  onSubmit: (value: string) => void;
  onEscape?: () => void;
  disabled?: boolean;
  placeholder?: string;
  maxHeight?: number;
  history?: string[];
  disableSubmit?: boolean;
}

/**
 * Reducer handles ALL state updates atomically
 * Single update = single render (no cursor flicker)
 */
function inputReducer(state: InputState, action: InputAction): InputState {
  switch (action.type) {
    case "INSERT_TEXT": {
      let nextState = state;
      const sanitized = action.text.replace(/\r/g, "");
      for (const char of sanitized) {
        if (char === "\n") {
          nextState = inputReducer(nextState, { type: "INSERT_NEWLINE" });
        } else {
          nextState = inputReducer(nextState, { type: "INSERT_CHAR", char });
        }
      }
      return nextState;
    }

    case "INSERT_CHAR": {
      const newLines = [...state.lines];
      const currentLine = newLines[state.cursorRow] || "";
      newLines[state.cursorRow] =
        currentLine.slice(0, state.cursorCol) +
        action.char +
        currentLine.slice(state.cursorCol);

      return {
        ...state,
        lines: newLines,
        cursorCol: state.cursorCol + action.char.length,
      };
    }

    case "INSERT_NEWLINE": {
      const newLines = [...state.lines];
      const currentLine = newLines[state.cursorRow] || "";
      const before = currentLine.slice(0, state.cursorCol);
      const after = currentLine.slice(state.cursorCol);
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
        newLines[state.cursorRow] =
          currentLine.slice(0, state.cursorCol - 1) +
          currentLine.slice(state.cursorCol);

        return {
          ...state,
          lines: newLines,
          cursorCol: state.cursorCol - 1,
        };
      } else if (state.cursorRow > 0) {
        const newLines = [...state.lines];
        const prevLine = newLines[state.cursorRow - 1] || "";
        const currentLine = newLines[state.cursorRow] || "";
        const newCol = prevLine.length;
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
      if (state.cursorCol < currentLine.length) {
        const newLines = [...state.lines];
        newLines[state.cursorRow] =
          currentLine.slice(0, state.cursorCol) +
          currentLine.slice(state.cursorCol + 1);

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
          cursorCol: Math.min(state.cursorCol, newLine.length),
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
          cursorCol: Math.min(state.cursorCol, newLine.length),
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
          cursorCol: prevLine.length,
        };
      }
      return state;
    }

    case "MOVE_RIGHT": {
      const currentLine = state.lines[state.cursorRow] || "";
      if (state.cursorCol < currentLine.length) {
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
      const newLines = action.value.split("\n");
      return {
        ...state,
        lines: newLines.length > 0 ? newLines : [""],
        cursorRow: newLines.length - 1,
        cursorCol: newLines[newLines.length - 1]?.length || 0,
      };
    }

    case "SET_HISTORY_INDEX":
      return {
        ...state,
        historyIndex: action.index,
        lines: action.value.split("\n"),
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

    default:
      return state;
  }
}

export const MultiLineInputOptimized = React.memo<MultiLineInputProps>(
  ({
    onSubmit,
    onEscape,
    disabled = false,
    placeholder = "Type your message...",
    maxHeight = 20,
    history = [],
    disableSubmit = false,
  }) => {
    const [state, dispatch] = useReducer(inputReducer, {
      lines: [""],
      cursorRow: 0,
      cursorCol: 0,
      scrollOffset: 0,
      historyIndex: history.length,
      draft: "",
    });
    const stateRef = React.useRef(state);

    // Update history index when history changes
    // But DON'T reset cursor/content - that's only for arrow key navigation
    const prevHistoryLengthRef = React.useRef(history.length);
    const pasteModeRef = React.useRef(false);

    useLayoutEffect(() => {
      stateRef.current = state;
    }, [state]);

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
      const value = getValue().trim();
      if (value) {
        onSubmit(value);
        dispatch({ type: "CLEAR" });
      }
    }, [onSubmit, getValue]);

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

    useInput((input, key) => {
      const current = stateRef.current;

      if (disabled) {
        return;
      }

      if (key.escape) {
        onEscape?.();
        return;
      }

      if (key.return) {
        if (key.meta || key.ctrl) {
          dispatch({ type: "INSERT_NEWLINE" });
        } else {
          if (!disableSubmit) {
            handleSubmit();
          }
        }
      } else if (key.delete || key.backspace) {
        dispatch({ type: "DELETE_CHAR" });
      } else if (key.upArrow) {
        if (key.shift) {
          if (current.cursorRow > 0) {
            dispatch({ type: "MOVE_UP" });
          }
        } else if (current.cursorRow === 0) {
          handleHistoryUp();
        } else {
          dispatch({ type: "MOVE_UP" });
        }
      } else if (key.downArrow) {
        if (key.shift) {
          if (current.cursorRow < current.lines.length - 1) {
            dispatch({ type: "MOVE_DOWN" });
          }
        } else if (current.cursorRow === current.lines.length - 1) {
          handleHistoryDown();
        } else {
          dispatch({ type: "MOVE_DOWN" });
        }
      } else if (key.leftArrow) {
        dispatch({ type: "MOVE_LEFT" });
      } else if (key.rightArrow) {
        dispatch({ type: "MOVE_RIGHT" });
      } else if (input && !key.ctrl && !key.meta) {
        if (input === "\x1b[200~") {
          pasteModeRef.current = true;
          return;
        }

        if (input === "\x1b[201~") {
          pasteModeRef.current = false;
          return;
        }

        const sanitized = input
          .replace(/\x1b\[200~/g, "")
          .replace(/\x1b\[201~/g, "")
          .replace(/\r/g, "");

        const isMouseEvent =
          !pasteModeRef.current &&
          (/^<\d+;\d+;\d+[Mm]$/.test(sanitized) ||
            (sanitized.startsWith("[") && sanitized.length > 1 && /[<M]/.test(sanitized)));

        if (!isMouseEvent && sanitized) {
          insertText(sanitized);
        }
      }
    });

    // Calculate visible lines (only render what fits on screen)
    const visibleHeight = Math.min(state.lines.length, maxHeight);
    const scrollOffset = React.useMemo(() => {
      // Auto-scroll to keep cursor visible
      if (state.cursorRow < state.scrollOffset) {
        return state.cursorRow;
      } else if (state.cursorRow >= state.scrollOffset + visibleHeight) {
        return state.cursorRow - visibleHeight + 1;
      }
      return state.scrollOffset;
    }, [state.cursorRow, state.scrollOffset, visibleHeight]);

    // Only render visible lines (huge perf improvement)
    const visibleLines = React.useMemo(() => {
      return state.lines.slice(scrollOffset, scrollOffset + visibleHeight);
    }, [state.lines, scrollOffset, visibleHeight]);

    return (
      <Box
        borderStyle="single"
        borderColor={TokyoNightTheme.border}
        paddingX={1}
        flexDirection="column"
        minHeight={1}
        width="100%"
      >
        {visibleLines.map((line, visibleIdx) => {
          const actualIdx = scrollOffset + visibleIdx;
          const isCurrentRow = actualIdx === state.cursorRow;
          const showPrompt = actualIdx === 0;

          const prompt = showPrompt ? "> " : "  ";
          const textColor = chalk.hex(TokyoNightTheme.inputText);

          let lineDisplay: string;
          if (isCurrentRow) {
            const beforeRaw = line.slice(0, state.cursorCol);
            const cursorRaw = line[state.cursorCol];
            const afterRaw = cursorRaw === undefined ? "" : line.slice(state.cursorCol + 1);
            const beforeColored = beforeRaw ? textColor(beforeRaw) : "";
            const cursorColored = chalk.inverse(cursorRaw ?? " ");
            const afterColored = afterRaw ? textColor(afterRaw) : "";
            lineDisplay = `${beforeColored}${cursorColored}${afterColored}`;
          } else {
            const displayValue = line || (actualIdx === 0 && !line ? placeholder : "");
            lineDisplay = displayValue ? textColor(displayValue) : "";
          }

          return (
            <Box key={actualIdx}>
              <Text color={TokyoNightTheme.inputPrompt}>{prompt}</Text>
              <Text>{lineDisplay}</Text>
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
      (prevProps.history?.length ?? 0) === (nextProps.history?.length ?? 0) &&
      prevProps.placeholder === nextProps.placeholder &&
      prevProps.maxHeight === nextProps.maxHeight &&
      prevProps.disabled === nextProps.disabled &&
      prevProps.disableSubmit === nextProps.disableSubmit &&
      prevProps.onSubmit === nextProps.onSubmit &&
      prevProps.onEscape === nextProps.onEscape
    );
  },
);
