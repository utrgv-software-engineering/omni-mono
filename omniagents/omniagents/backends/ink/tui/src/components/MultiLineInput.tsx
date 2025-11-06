/**
 * Multi-line text input component with history navigation
 */

import React, { useState, useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import { TokyoNightTheme } from '../theme.js';

export interface MultiLineInputProps {
  onSubmit: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  maxHeight?: number; // Maximum number of visible lines
  history?: string[]; // History items for up/down navigation
}

export const MultiLineInput = React.memo<MultiLineInputProps>(({
  onSubmit,
  disabled = false,
  placeholder = 'Type your message...',
  maxHeight = 20,
  history = [],
}) => {
  const [lines, setLines] = useState<string[]>(['']);
  const [cursorRow, setCursorRow] = useState(0);
  const [cursorCol, setCursorCol] = useState(0);
  const [scrollOffset, setScrollOffset] = useState(0);
  const [historyIndex, setHistoryIndex] = useState(history.length);
  const [draft, setDraft] = useState('');

  // Update history index when history changes
  useEffect(() => {
    setHistoryIndex(history.length);
  }, [history]);

  const getValue = () => lines.join('\n');

  const setValue = (value: string) => {
    const newLines = value.split('\n');
    setLines(newLines.length > 0 ? newLines : ['']);
    setCursorRow(newLines.length - 1);
    setCursorCol(newLines[newLines.length - 1]?.length || 0);
  };

  const insertChar = (char: string) => {
    const newLines = [...lines];
    const currentLine = newLines[cursorRow] || '';
    newLines[cursorRow] = currentLine.slice(0, cursorCol) + char + currentLine.slice(cursorCol);
    setLines(newLines);
    setCursorCol(cursorCol + 1);
  };

  const insertNewline = () => {
    const newLines = [...lines];
    const currentLine = newLines[cursorRow] || '';
    const before = currentLine.slice(0, cursorCol);
    const after = currentLine.slice(cursorCol);
    newLines[cursorRow] = before;
    newLines.splice(cursorRow + 1, 0, after);
    setLines(newLines);
    setCursorRow(cursorRow + 1);
    setCursorCol(0);
  };

  const deleteChar = () => {
    if (cursorCol > 0) {
      // Delete character before cursor on current line
      const newLines = [...lines];
      const currentLine = newLines[cursorRow] || '';
      newLines[cursorRow] = currentLine.slice(0, cursorCol - 1) + currentLine.slice(cursorCol);
      setLines(newLines);
      setCursorCol(cursorCol - 1);
    } else if (cursorRow > 0) {
      // At start of line, merge with previous line
      const newLines = [...lines];
      const prevLine = newLines[cursorRow - 1] || '';
      const currentLine = newLines[cursorRow] || '';
      const newCol = prevLine.length;
      newLines[cursorRow - 1] = prevLine + currentLine;
      newLines.splice(cursorRow, 1);
      setLines(newLines);
      setCursorRow(cursorRow - 1);
      setCursorCol(newCol);
    }
  };

  const deleteCharForward = () => {
    const currentLine = lines[cursorRow] || '';
    if (cursorCol < currentLine.length) {
      // Delete character after cursor
      const newLines = [...lines];
      newLines[cursorRow] = currentLine.slice(0, cursorCol) + currentLine.slice(cursorCol + 1);
      setLines(newLines);
    } else if (cursorRow < lines.length - 1) {
      // At end of line, merge with next line
      const newLines = [...lines];
      const nextLine = newLines[cursorRow + 1] || '';
      newLines[cursorRow] = currentLine + nextLine;
      newLines.splice(cursorRow + 1, 1);
      setLines(newLines);
    }
  };

  const moveUp = () => {
    // If at first line, navigate history
    if (cursorRow === 0) {
      if (history.length === 0) return;

      if (historyIndex === history.length) {
        // Save current draft
        setDraft(getValue());
      }

      if (historyIndex > 0) {
        const newIndex = historyIndex - 1;
        setHistoryIndex(newIndex);
        setValue(history[newIndex]);
      }
    } else {
      // Move cursor up one line
      const newRow = cursorRow - 1;
      const newLine = lines[newRow] || '';
      setCursorRow(newRow);
      setCursorCol(Math.min(cursorCol, newLine.length));
    }
  };

  const moveDown = () => {
    // If at last line, navigate history
    if (cursorRow === lines.length - 1) {
      if (history.length === 0) return;

      if (historyIndex < history.length - 1) {
        const newIndex = historyIndex + 1;
        setHistoryIndex(newIndex);
        setValue(history[newIndex]);
      } else if (historyIndex === history.length - 1) {
        // Restore draft
        setHistoryIndex(history.length);
        setValue(draft);
      }
    } else {
      // Move cursor down one line
      const newRow = cursorRow + 1;
      const newLine = lines[newRow] || '';
      setCursorRow(newRow);
      setCursorCol(Math.min(cursorCol, newLine.length));
    }
  };

  const moveLeft = () => {
    if (cursorCol > 0) {
      setCursorCol(cursorCol - 1);
    } else if (cursorRow > 0) {
      const prevLine = lines[cursorRow - 1] || '';
      setCursorRow(cursorRow - 1);
      setCursorCol(prevLine.length);
    }
  };

  const moveRight = () => {
    const currentLine = lines[cursorRow] || '';
    if (cursorCol < currentLine.length) {
      setCursorCol(cursorCol + 1);
    } else if (cursorRow < lines.length - 1) {
      setCursorRow(cursorRow + 1);
      setCursorCol(0);
    }
  };

  const handleSubmit = () => {
    const value = getValue().trim();
    if (value) {
      onSubmit(value);
      setLines(['']);
      setCursorRow(0);
      setCursorCol(0);
      setScrollOffset(0);
      setHistoryIndex(history.length);
      setDraft('');
    }
  };

  useInput((input, key) => {
    if (disabled) return;

    // Handle special keys
    if (key.return) {
      if (key.meta || key.ctrl) {
        // Alt+Enter or Ctrl+J: insert newline
        insertNewline();
      } else {
        // Enter: submit
        handleSubmit();
      }
    } else if (key.delete || key.backspace) {
      // Backspace (Ink reports it as delete on some systems)
      deleteChar();
    } else if (key.upArrow) {
      moveUp();
    } else if (key.downArrow) {
      moveDown();
    } else if (key.leftArrow) {
      moveLeft();
    } else if (key.rightArrow) {
      moveRight();
    } else if (input && !key.ctrl && !key.meta) {
      // Regular character input
      insertChar(input);
    }
  }, { isActive: !disabled });

  // Calculate visible lines based on scrollOffset
  const visibleHeight = Math.min(lines.length, maxHeight);

  // Auto-scroll to keep cursor visible
  useEffect(() => {
    if (cursorRow < scrollOffset) {
      setScrollOffset(cursorRow);
    } else if (cursorRow >= scrollOffset + visibleHeight) {
      setScrollOffset(cursorRow - visibleHeight + 1);
    }
  }, [cursorRow, scrollOffset, visibleHeight]);

  const visibleLines = lines.slice(scrollOffset, scrollOffset + visibleHeight);

  // Render the input box
  return (
    <Box
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
      paddingX={1}
      flexDirection="column"
      minHeight={1}
      width="100%"
    >
      {visibleLines.map((line, idx) => {
        const actualRow = scrollOffset + idx;
        const isCurrentRow = actualRow === cursorRow;
        const showPrompt = idx === 0;

        return (
          <Box key={idx}>
            {showPrompt ? (
              <Text color={TokyoNightTheme.inputPrompt}>{'> '}</Text>
            ) : (
              <Text>{'  '}</Text>
            )}
            {isCurrentRow ? (
              // Current line with cursor
              <Text>
                <Text color={TokyoNightTheme.inputText}>{line.slice(0, cursorCol)}</Text>
                <Text inverse>{line[cursorCol] || ' '}</Text>
                <Text color={TokyoNightTheme.inputText}>{line.slice(cursorCol + 1)}</Text>
              </Text>
            ) : (
              // Other lines
              <Text color={TokyoNightTheme.inputText}>{line}</Text>
            )}
          </Box>
        );
      })}
    </Box>
  );
}, (prevProps, nextProps) => {
  // Only re-render if disabled state or history length changes
  // Don't re-render for new onSubmit callback references
  return prevProps.disabled === nextProps.disabled &&
         prevProps.history.length === nextProps.history.length &&
         prevProps.placeholder === nextProps.placeholder &&
         prevProps.maxHeight === nextProps.maxHeight;
});
