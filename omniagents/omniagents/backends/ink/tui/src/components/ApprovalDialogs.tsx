/**
 * Specialized approval dialog components for different metadata types
 * Matches Bubbletea's approval renderers: renderDiffApproval, renderFileWriteApproval, renderCommandApproval
 */

import React from "react";
import { Box, Text } from "ink";
import { Theme } from "../theme.js";
import type { RichMetadata } from "../types/richMetadata.js";
import { DialogShell } from "./shared/DialogShell.js";
import {
  BaseSelectionList,
  type SelectionOption,
} from "./shared/BaseSelectionList.js";
import { themedMemo } from "../theme/themed-memo.js";

export type ApprovalOption = SelectionOption<"yes" | "always" | "no">;

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

const APPROVAL_FOOTER = "Use ↑↓ to navigate, Enter to select, ESC to deny";

interface BaseApprovalProps {
  onApprove: (value: "yes" | "always" | "no") => void;
}

/**
 * Diff approval dialog
 * Matches Bubbletea's renderDiffApprovalWithWidth
 */
export const DiffApproval = themedMemo<
  BaseApprovalProps & { tool: string; metadata: RichMetadata }
>(({ tool, metadata, onApprove }) => {
  let filePath = "";
  if (metadata.metadata && typeof metadata.metadata.file_path === "string") {
    filePath = metadata.metadata.file_path;
  }

  const title = filePath
    ? `Tool Approval: ${tool} - ${filePath}`
    : `Tool Approval: ${tool}`;

  const diffLines = (metadata.value?.diff_lines as string[]) || [];

  return (
    <DialogShell title={title} footer={APPROVAL_FOOTER}>
      {diffLines.length > 0 && (
        <Box flexDirection="column">
          <Text color={Theme.textChrome}>Changes:</Text>
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
                  <Text key={idx} color={Theme.textChrome}>
                    {lineStr}
                  </Text>
                );
              }

              // Addition
              if (lineStr.startsWith("+") && !lineStr.startsWith("+++")) {
                return (
                  <Text key={idx} color={Theme.stateActive}>
                    {lineStr}
                  </Text>
                );
              }

              // Deletion
              if (lineStr.startsWith("-") && !lineStr.startsWith("---")) {
                return (
                  <Text key={idx} color={Theme.stateFailed}>
                    {lineStr}
                  </Text>
                );
              }

              // Context
              return (
                <Text key={idx} color={Theme.textChrome}>
                  {lineStr}
                </Text>
              );
            })}
          </Box>
        </Box>
      )}

      <BaseSelectionList options={DEFAULT_OPTIONS} onSelect={onApprove} />
    </DialogShell>
  );
});

/**
 * File write approval dialog
 * Matches Bubbletea's renderFileWriteApprovalWithWidth
 */
export const FileWriteApproval = themedMemo<
  BaseApprovalProps & { tool: string; metadata: RichMetadata }
>(({ metadata, onApprove }) => {
  const summary = metadata.summary || "File Write";
  const content =
    (typeof metadata.value?.content === "string"
      ? metadata.value.content
      : undefined) ||
    metadata.preview ||
    "";

  return (
    <DialogShell title={summary} footer={APPROVAL_FOOTER}>
      {content && (
        <Box flexDirection="column">
          <Text color={Theme.textChrome}>Content:</Text>
          <Box flexDirection="column">
            {content
              .split("\n")
              .slice(0, 15)
              .map((line, idx) => (
                <Text key={idx} color={Theme.textSecondary}>
                  {line}
                </Text>
              ))}
            {content.split("\n").length > 15 && (
              <Text color={Theme.textChrome} italic>
                ... {content.split("\n").length - 15} more lines
              </Text>
            )}
          </Box>
        </Box>
      )}

      <BaseSelectionList options={DEFAULT_OPTIONS} onSelect={onApprove} />
    </DialogShell>
  );
});

/**
 * Command approval dialog
 * Matches Bubbletea's renderCommandApprovalWithWidth
 */
export const CommandApproval = themedMemo<
  BaseApprovalProps & {
    tool: string;
    metadata: RichMetadata;
    arguments: string;
  }
>(({ metadata, arguments: args, onApprove }) => {
  const summary = metadata.summary || "Run Command";

  let command = "";
  if (metadata.metadata && typeof metadata.metadata.command === "string") {
    command = metadata.metadata.command;
  }
  if (!command) {
    command = args;
  }

  return (
    <DialogShell title={summary} footer={APPROVAL_FOOTER}>
      {command && (
        <Box flexDirection="column">
          <Text color={Theme.textChrome}>Command:</Text>
          <Text color={Theme.stateActive}>$ {command}</Text>
        </Box>
      )}

      <BaseSelectionList options={DEFAULT_OPTIONS} onSelect={onApprove} />
    </DialogShell>
  );
});
