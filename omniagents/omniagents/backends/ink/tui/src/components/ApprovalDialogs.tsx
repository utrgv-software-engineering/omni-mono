/**
 * Specialized approval dialog components for different metadata types
 * Matches Bubbletea's approval renderers: renderDiffApproval, renderFileWriteApproval, renderCommandApproval
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import { TokyoNightTheme } from "../theme.js";
import type { RichMetadata } from "../types/richMetadata.js";

export interface ApprovalOption {
  title: string;
  description: string;
  value: "yes" | "always" | "no";
}

const DEFAULT_OPTIONS: ApprovalOption[] = [
  {
    title: "1. Approve Once",
    description: "Allow this tool call only",
    value: "yes",
  },
  {
    title: "2. Always Approve",
    description: "Allow this and future calls in this session",
    value: "always",
  },
  {
    title: "3. Deny",
    description: "Cancel this tool call",
    value: "no",
  },
];

interface BaseApprovalProps {
  onApprove: (value: "yes" | "always" | "no") => void;
}

/**
 * Diff approval dialog
 * Matches Bubbletea's renderDiffApprovalWithWidth
 */
export const DiffApproval = React.memo<
  BaseApprovalProps & { tool: string; metadata: RichMetadata }
>(({ tool, metadata, onApprove }) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  useInput((input, key) => {
    if (key.shift && (key.upArrow || key.downArrow)) {
      return;
    }
    if (key.upArrow) {
      setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
    } else if (key.downArrow) {
      setSelectedIndex((prev) =>
        prev < DEFAULT_OPTIONS.length - 1 ? prev + 1 : prev,
      );
    } else if (key.return) {
      onApprove(DEFAULT_OPTIONS[selectedIndex].value);
    } else if (input === "1") {
      onApprove("yes");
    } else if (input === "2") {
      onApprove("always");
    } else if (input === "3") {
      onApprove("no");
    }
  });

  // Extract file path from metadata
  let filePath = "";
  if (metadata.metadata && typeof metadata.metadata.file_path === "string") {
    filePath = metadata.metadata.file_path;
  }

  const title = filePath
    ? `Tool Approval: ${tool} - ${filePath}`
    : `Tool Approval: ${tool}`;

  // Extract diff lines from value
  const diffLines = (metadata.value?.diff_lines as string[]) || [];

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
      paddingX={2}
      paddingY={1}
    >
      <Box marginBottom={1}>
        <Text bold color={TokyoNightTheme.approvalTitle}>
          {title}
        </Text>
      </Box>

        {diffLines.length > 0 && (
          <Box marginBottom={1} flexDirection="column">
            <Text color={TokyoNightTheme.defaultText}>Changes:</Text>
            <Box flexDirection="column">
              {diffLines.map((line, idx) => {
                const lineStr = line.replace(/\n$/, "");

              // Skip file headers
              if (lineStr.startsWith("+++") || lineStr.startsWith("---")) {
                return null;
              }

              // Hunk header
                if (lineStr.startsWith("@@")) {
                  return (
                    <Text key={idx} color={TokyoNightTheme.defaultText}>
                      {lineStr}
                    </Text>
                  );
                }

              // Addition
              if (lineStr.startsWith("+") && !lineStr.startsWith("+++")) {
                return (
                  <Text key={idx} color={TokyoNightTheme.info}>
                    {lineStr}
                  </Text>
                );
              }

              // Deletion
              if (lineStr.startsWith("-") && !lineStr.startsWith("---")) {
                return (
                  <Text key={idx} color={TokyoNightTheme.error}>
                    {lineStr}
                  </Text>
                );
              }

              // Context
                return (
                  <Text key={idx} color={TokyoNightTheme.defaultText}>
                    {lineStr}
                  </Text>
                );
              })}
            </Box>
          </Box>
        )}

      <Box flexDirection="column" marginBottom={1}>
        {DEFAULT_OPTIONS.map((option, index) => (
          <Box key={option.value}>
            {index === selectedIndex ? (
              <Text bold color={TokyoNightTheme.approvalSelected}>
                ▶ {option.title} - {option.description}
              </Text>
            ) : (
              <Text color={TokyoNightTheme.approvalUnselected}>
                {option.title} - {option.description}
              </Text>
            )}
          </Box>
        ))}
      </Box>

      <Box>
        <Text color={TokyoNightTheme.defaultText}>
          Use ↑↓ to navigate, Enter to select, ESC to deny
        </Text>
      </Box>
    </Box>
  );
});

/**
 * File write approval dialog
 * Matches Bubbletea's renderFileWriteApprovalWithWidth
 */
export const FileWriteApproval = React.memo<
  BaseApprovalProps & { tool: string; metadata: RichMetadata }
>(({ tool, metadata, onApprove }) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  useInput((input, key) => {
    if (key.shift && (key.upArrow || key.downArrow)) {
      return;
    }
    if (key.upArrow) {
      setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
    } else if (key.downArrow) {
      setSelectedIndex((prev) =>
        prev < DEFAULT_OPTIONS.length - 1 ? prev + 1 : prev,
      );
    } else if (key.return) {
      onApprove(DEFAULT_OPTIONS[selectedIndex].value);
    } else if (input === "1") {
      onApprove("yes");
    } else if (input === "2") {
      onApprove("always");
    } else if (input === "3") {
      onApprove("no");
    }
  });

  const summary = metadata.summary || "File Write";
  const content = (metadata.value as string) || metadata.preview || "";

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
      paddingX={2}
      paddingY={1}
    >
      <Box marginBottom={1}>
        <Text bold color={TokyoNightTheme.approvalTitle}>
          {summary}
        </Text>
      </Box>

      {content && (
        <Box marginBottom={1} flexDirection="column">
          <Text color={TokyoNightTheme.defaultText}>Content:</Text>
          <Box flexDirection="column">
            {content
              .split("\n")
              .slice(0, 15)
              .map((line, idx) => (
                <Text key={idx} color={TokyoNightTheme.toolResult}>
                  {line}
                </Text>
              ))}
            {content.split("\n").length > 15 && (
              <Text color={TokyoNightTheme.defaultText} italic>
                ... {content.split("\n").length - 15} more lines
              </Text>
            )}
          </Box>
        </Box>
      )}

      <Box flexDirection="column" marginBottom={1}>
        {DEFAULT_OPTIONS.map((option, index) => (
          <Box key={option.value}>
            {index === selectedIndex ? (
              <Text bold color={TokyoNightTheme.approvalSelected}>
                ▶ {option.title} - {option.description}
              </Text>
            ) : (
              <Text color={TokyoNightTheme.approvalUnselected}>
                {option.title} - {option.description}
              </Text>
            )}
          </Box>
        ))}
      </Box>

      <Box>
        <Text color={TokyoNightTheme.defaultText}>
          Use ↑↓ to navigate, Enter to select, ESC to deny
        </Text>
      </Box>
    </Box>
  );
});

/**
 * Command approval dialog
 * Matches Bubbletea's renderCommandApprovalWithWidth
 */
export const CommandApproval = React.memo<
  BaseApprovalProps & {
    tool: string;
    metadata: RichMetadata;
    arguments: string;
  }
>(({ tool, metadata, arguments: args, onApprove }) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  useInput((input, key) => {
    if (key.shift && (key.upArrow || key.downArrow)) {
      return;
    }
    if (key.upArrow) {
      setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
    } else if (key.downArrow) {
      setSelectedIndex((prev) =>
        prev < DEFAULT_OPTIONS.length - 1 ? prev + 1 : prev,
      );
    } else if (key.return) {
      onApprove(DEFAULT_OPTIONS[selectedIndex].value);
    } else if (input === "1") {
      onApprove("yes");
    } else if (input === "2") {
      onApprove("always");
    } else if (input === "3") {
      onApprove("no");
    }
  });

  const summary = metadata.summary || "Run Command";

  // Extract command from metadata
  let command = "";
  if (metadata.metadata && typeof metadata.metadata.command === "string") {
    command = metadata.metadata.command;
  }

  // Fallback to arguments if command not in metadata
  if (!command) {
    command = args;
  }

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={TokyoNightTheme.border}
      paddingX={2}
      paddingY={1}
    >
      <Box marginBottom={1}>
        <Text bold color={TokyoNightTheme.approvalTitle}>
          {summary}
        </Text>
      </Box>

      {command && (
        <Box marginBottom={1} flexDirection="column">
          <Text color={TokyoNightTheme.defaultText}>Command:</Text>
          <Text color={TokyoNightTheme.info}>$ {command}</Text>
        </Box>
      )}

      <Box flexDirection="column" marginBottom={1}>
        {DEFAULT_OPTIONS.map((option, index) => (
          <Box key={option.value}>
            {index === selectedIndex ? (
              <Text bold color={TokyoNightTheme.approvalSelected}>
                ▶ {option.title} - {option.description}
              </Text>
            ) : (
              <Text color={TokyoNightTheme.approvalUnselected}>
                {option.title} - {option.description}
              </Text>
            )}
          </Box>
        ))}
      </Box>

      <Box>
        <Text color={TokyoNightTheme.defaultText}>
          Use ↑↓ to navigate, Enter to select, ESC to deny
        </Text>
      </Box>
    </Box>
  );
});
