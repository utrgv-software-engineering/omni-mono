/**
 * MCP Tool approval dialog — parallel to ApprovalDialog but for hosted
 * MCP tool calls (HostedMCPTool). Identifies the MCP server via
 * ``server_label`` and intentionally omits the "Always" affordance since
 * the SDK keys MCP approvals by ``(server_label, tool_name)`` and we do
 * not maintain a parallel always-allow set on this path.
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import { TokyoNightTheme } from "../theme.js";

export interface McpApprovalOption {
  title: string;
  description: string;
  value: "yes" | "no";
}

export interface McpApprovalDialogProps {
  tool: string;
  serverLabel: string;
  arguments: string;
  onApprove: (value: "yes" | "no") => void;
}

const DEFAULT_OPTIONS: McpApprovalOption[] = [
  {
    title: "1. Approve",
    description: "Allow this MCP tool call",
    value: "yes",
  },
  {
    title: "2. Deny",
    description: "Cancel this MCP tool call",
    value: "no",
  },
];

export const McpApprovalDialog = React.memo<McpApprovalDialogProps>(
  ({ tool, serverLabel, arguments: args, onApprove }) => {
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
        onApprove("no");
      }
    });

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
            MCP Tool Approval: {tool}
          </Text>
        </Box>

        <Box marginBottom={1}>
          <Text color={TokyoNightTheme.defaultText}>
            Server:{" "}
            <Text color={TokyoNightTheme.toolResult}>{serverLabel}</Text>
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
    return (
      prevProps.tool === nextProps.tool &&
      prevProps.serverLabel === nextProps.serverLabel &&
      prevProps.arguments === nextProps.arguments
    );
  },
);
