/**
 * Tool approval dialog component - routes to specialized dialogs based on metadata
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import { TokyoNightTheme } from "../theme.js";
import { parseRichMetadata } from "../types/richMetadata.js";
import {
  DiffApproval,
  FileWriteApproval,
  CommandApproval,
} from "./ApprovalDialogs.js";

export interface ApprovalOption {
  title: string;
  description: string;
  value: "yes" | "always" | "no";
}

export interface ApprovalDialogProps {
  tool: string;
  arguments: string;
  metadata?: Record<string, unknown>;
  onApprove: (value: "yes" | "always" | "no") => void;
}

// Memoize options outside component (constant)
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

export const ApprovalDialog = React.memo<ApprovalDialogProps>(
  ({ tool, arguments: args, metadata, onApprove }) => {
    // Memoize rich metadata parsing
    const richMetadata = React.useMemo(
      () => (metadata ? parseRichMetadata(metadata) : null),
      [metadata],
    );

    if (richMetadata?.display_type === "diff") {
      return (
        <DiffApproval
          tool={tool}
          metadata={richMetadata}
          onApprove={onApprove}
        />
      );
    }

    if (richMetadata?.display_type === "file_write") {
      return (
        <FileWriteApproval
          tool={tool}
          metadata={richMetadata}
          onApprove={onApprove}
        />
      );
    }

    if (richMetadata?.display_type === "command") {
      return (
        <CommandApproval
          tool={tool}
          metadata={richMetadata}
          arguments={args}
          onApprove={onApprove}
        />
      );
    }

    // Default: text-based approval
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
        // Enter key - select current option
        onApprove(DEFAULT_OPTIONS[selectedIndex].value);
      } else if (input === "1") {
        onApprove("yes");
      } else if (input === "2") {
        onApprove("always");
      } else if (input === "3") {
        onApprove("no");
      }
    });

    // Memoize arguments parsing
    const argsDisplay = React.useMemo(() => {
      try {
        const parsed = JSON.parse(args);
        return JSON.stringify(parsed, null, 2);
      } catch (e) {
        return args;
      }
    }, [args]);

    return (
      <Box
        flexDirection="column"
        borderStyle="single"
        borderColor={TokyoNightTheme.border}
        paddingX={2}
        paddingY={1}
      >
        <Box marginBottom={1}>
          <Text bold color={TokyoNightTheme.approvalTitle}>
            Tool Approval: {tool}
          </Text>
        </Box>

        <Box marginBottom={1} flexDirection="column">
          <Text color={TokyoNightTheme.defaultText}>Arguments:</Text>
          <Text color={TokyoNightTheme.toolResult}>
            {argsDisplay.length > 200
              ? argsDisplay.slice(0, 200) + "..."
              : argsDisplay}
          </Text>
        </Box>

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
  },
  (prevProps, nextProps) => {
    // Only re-render if tool, arguments, or metadata actually changed
    return (
      prevProps.tool === nextProps.tool &&
      prevProps.arguments === nextProps.arguments &&
      JSON.stringify(prevProps.metadata) === JSON.stringify(nextProps.metadata)
    );
  },
);
