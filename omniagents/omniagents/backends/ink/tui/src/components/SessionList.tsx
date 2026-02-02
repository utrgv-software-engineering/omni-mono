/**
 * Session list component for selecting a previous session
 * Matches Bubbletea's renderSessionList
 */

import React, { useMemo } from "react";
import { Box, Text, useStdout } from "ink";
import { TokyoNightTheme } from "../theme.js";

export interface Session {
  id: string;
  created_at: string;
  archived: boolean;
  message_count: number;
  first_message?: unknown;
  last_message?: unknown;
}

export interface SessionListProps {
  sessions: Session[];
  selectedIndex: number;
}

/**
 * Extract text content from message (can be dict with role/content or plain string)
 */
function extractMessageContent(msg: unknown): string {
  if (!msg) return "";

  if (typeof msg === "string") {
    return msg;
  }

  if (typeof msg === "object" && msg !== null) {
    const obj = msg as Record<string, unknown>;

    // Check for content field
    if ("content" in obj) {
      const content = obj.content;
      if (typeof content === "string") {
        return content;
      }
      if (Array.isArray(content)) {
        for (const part of content) {
          if (typeof part === "string") return part;
          if (part && typeof part === "object" && "text" in part) {
            return String((part as any).text);
          }
        }
      }
    }
  }

  return "";
}

/**
 * Truncate and clean message for display
 */
function truncateMessage(content: string, maxLength: number): string {
  const trimmed = content.trim();
  if (!trimmed) return "";

  // Take first line only
  const firstLine = trimmed.split("\n")[0];

  if (firstLine.length > maxLength) {
    return firstLine.slice(0, maxLength - 3) + "...";
  }
  return firstLine;
}

export const SessionList = React.memo<SessionListProps>(
  ({ sessions, selectedIndex }) => {
    const { stdout } = useStdout();
    const terminalHeight = stdout?.rows || 24;

    // Calculate how many sessions we can display
    // Reserve space for:
    // - StatusBar ASCII art (3 lines) + paddingY (2 lines): 5 lines
    // - Margin after status bar (marginTop={1} in App): 1 line
    // - Session list header: 1 line
    // - Margin after header (marginBottom={1}): 1 line
    // - Margin before help (marginTop={1}): 1 line
    // - Help text: 1 line
    // - Bottom safety margin: 1 line
    // Total: 11 lines reserved
    const maxVisibleSessions = Math.max(5, terminalHeight - 11);

    // Calculate pagination
    const { visibleSessions, currentPage, totalPages } = useMemo(() => {
      if (sessions.length === 0) {
        return { visibleSessions: [], currentPage: 0, totalPages: 0 };
      }

      // Calculate which page the selected item is on
      const page = Math.floor(selectedIndex / maxVisibleSessions);
      const total = Math.ceil(sessions.length / maxVisibleSessions);

      // Get the sessions for the current page
      const start = page * maxVisibleSessions;
      const end = Math.min(start + maxVisibleSessions, sessions.length);
      const visible = sessions.slice(start, end);

      return { visibleSessions: visible, currentPage: page, totalPages: total };
    }, [sessions, selectedIndex, maxVisibleSessions]);

    if (sessions.length === 0) {
      return (
        <Box flexDirection="column" paddingX={1}>
          <Text color={TokyoNightTheme.defaultText}>
            No sessions found. Press Tab to create a new session.
          </Text>
        </Box>
      );
    }

    return (
      <Box flexDirection="column" paddingX={1}>
        <Box marginBottom={1}>
          <Text bold color={TokyoNightTheme.sessionTitle}>
            Select a session or press Tab to create new:
          </Text>
        </Box>

        {visibleSessions.map((session, visibleIdx) => {
          // Calculate the actual index in the full sessions array
          const actualIndex = currentPage * maxVisibleSessions + visibleIdx;
          const isSelected = actualIndex === selectedIndex;
          const cursor = isSelected ? "> " : "  ";

          // Format session ID (truncate if needed)
          const sid =
            session.id.length > 8 ? session.id.slice(0, 8) : session.id;

          // Format timestamp
          let timestamp = "";
          if (session.created_at) {
            const date = new Date(session.created_at);
            timestamp = date.toISOString().slice(0, 10); // YYYY-MM-DD
          }

          // Build session line
          let line = `${cursor}${sid}`;
          if (timestamp) {
            line += ` • ${timestamp}`;
          }
          line += ` • ${session.message_count} messages`;

          if (session.archived) {
            line += " [archived]";
          }

          // Add last message preview
          const lastMsg = session.last_message || session.first_message;
          if (lastMsg) {
            const content = extractMessageContent(lastMsg);
            const preview = truncateMessage(content, 50);
            if (preview) {
              line += ` • ${preview}`;
            }
          }

          const textColor = isSelected
            ? TokyoNightTheme.sessionTitle
            : TokyoNightTheme.help;

          return (
            <Box key={session.id}>
              <Text color={textColor} bold={isSelected}>
                {line}
              </Text>
            </Box>
          );
        })}

        <Box marginTop={1}>
          <Text color={TokyoNightTheme.help}>
            ↑↓ navigate • Enter select • Tab new session • Ctrl+C quit
            {totalPages > 1 && ` • Page ${currentPage + 1}/${totalPages}`}
          </Text>
        </Box>
      </Box>
    );
  },
  (prev, next) => {
    return (
      prev.selectedIndex === next.selectedIndex &&
      prev.sessions.length === next.sessions.length
    );
  },
);
