/**
 * MCP Tool approval dialog — parallel to ApprovalDialog but for hosted
 * MCP tool calls (HostedMCPTool). Identifies the MCP server via
 * ``server_label`` and intentionally omits the "Always" affordance since
 * the SDK keys MCP approvals by ``(server_label, tool_name)`` and we do
 * not maintain a parallel always-allow set on this path.
 */

import React from "react";
import { Box, Text } from "ink";
import { TokyoNightTheme } from "../theme.js";
import { DialogShell } from "./shared/DialogShell.js";
import {
  BaseSelectionList,
  type SelectionOption,
} from "./shared/BaseSelectionList.js";

export type McpApprovalOption = SelectionOption<"yes" | "no">;

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
    const argsDisplay = React.useMemo(() => {
      try {
        const parsed = JSON.parse(args);
        return JSON.stringify(parsed, null, 2);
      } catch (e) {
        return args;
      }
    }, [args]);

    return (
      <DialogShell
        title={`MCP Tool Approval: ${tool}`}
        footer="Use ↑↓ to navigate, Enter to select, ESC to deny"
      >
        <Box>
          <Text color={TokyoNightTheme.defaultText}>
            Server:{" "}
            <Text color={TokyoNightTheme.toolResult}>{serverLabel}</Text>
          </Text>
        </Box>

        <Box flexDirection="column">
          <Text color={TokyoNightTheme.defaultText}>Arguments:</Text>
          <Text color={TokyoNightTheme.toolResult}>
            {argsDisplay.length > 200
              ? argsDisplay.slice(0, 200) + "..."
              : argsDisplay}
          </Text>
        </Box>

        <BaseSelectionList options={DEFAULT_OPTIONS} onSelect={onApprove} />
      </DialogShell>
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
