/**
 * Tool approval dialog component - routes to specialized dialogs based on metadata
 */

import React from "react";
import { Box, Text } from "ink";
import { Theme } from "../theme.js";
import { parseRichMetadata } from "../types/richMetadata.js";
import {
  DiffApproval,
  FileWriteApproval,
  CommandApproval,
} from "./ApprovalDialogs.js";
import { DialogShell } from "./shared/DialogShell.js";
import {
  BaseSelectionList,
  type SelectionOption,
} from "./shared/BaseSelectionList.js";

export type ApprovalOption = SelectionOption<"yes" | "always" | "no">;

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

    // Memoize arguments parsing
    const argsDisplay = React.useMemo(() => {
      try {
        const parsed = JSON.parse(args);
        return JSON.stringify(parsed, null, 2);
      } catch (e) {
        return args;
      }
    }, [args]);

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

    return (
      <DialogShell
        title={`Tool Approval: ${tool}`}
        footer="Use ↑↓ to navigate, Enter to select, ESC to deny"
      >
        <Box flexDirection="column">
          <Text color={Theme.textChrome}>Arguments:</Text>
          <Text color={Theme.textSecondary}>
            {argsDisplay.length > 200
              ? argsDisplay.slice(0, 200) + "..."
              : argsDisplay}
          </Text>
        </Box>
        <BaseSelectionList options={DEFAULT_OPTIONS} onSelect={onApprove} />
      </DialogShell>
    );
  },
);
