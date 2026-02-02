/**
 * Optimized message list with virtual scrolling
 * Alternate buffer eliminates need for Static component
 */

import React, { useMemo } from "react";
import { Box, Text } from "ink";
import { useMessages } from "../store/appStore.js";
import type { Message } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";
import { formatToolArgumentsSmart } from "../utils/formatArguments.js";
import { renderMarkdownCached } from "../utils/markdownCache.js";
import { parseRichMetadata } from "../types/richMetadata.js";
import { RichToolOutput } from "../utils/richToolOutput.js";

export const MessageListOptimized = React.memo(() => {
  const messages = useMessages();

  if (messages.length === 0) {
    return null;
  }

  return (
    <Box flexDirection="column">
      {messages.map((message, index) => {
        // Generate a safe key, handling cases where content/role might be undefined
        const contentPreview = message.content
          ? message.content.slice(0, 20)
          : "empty";
        const role = message.role || "unknown";

        const key =
          message.toolCall?.call_id ||
          (message.timestamp
            ? `${role}-${message.timestamp.getTime()}`
            : undefined) ||
          `${role}-${contentPreview}-${index}`;

        return <MessageItem key={key} message={message} />;
      })}
    </Box>
  );
});

// Individual message memoized with deep equality check
const MessageItem = React.memo<{ message: Message }>(
  ({ message }) => {
    switch (message.role) {
      case "user":
        return <UserMessage message={message} />;
      case "assistant":
        return <AssistantMessage message={message} />;
      case "tool_call":
        return <ToolCallMessage message={message} />;
      case "approval_request":
        // Approval requests shown in ApprovalDialog, not in message history
        return null;
      default:
        return null;
    }
  },
  (prev, next) => {
    // Deep equality check - only re-render if content actually changed
    if (prev.message.role !== next.message.role) return false;
    if (prev.message.content !== next.message.content) return false;

    // For tool calls, check if output changed
    if (prev.message.toolCall && next.message.toolCall) {
      return (
        prev.message.toolCall.output === next.message.toolCall.output &&
        prev.message.toolCall.running === next.message.toolCall.running &&
        prev.message.toolCall.metadata === next.message.toolCall.metadata
      );
    }

    // If one has toolCall and the other doesn't, they're different
    if (prev.message.toolCall !== next.message.toolCall) return false;

    return true;
  },
);

const UserMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    const prefix = "> ";
    const prefixWidth = prefix.length;

    return (
      <Box flexDirection="column" marginBottom={1}>
        <Box flexDirection="row">
          <Box width={prefixWidth}>
            <Text color={TokyoNightTheme.messagePrompt}>{prefix}</Text>
          </Box>
          <Box flexGrow={1}>
            <Text color={TokyoNightTheme.userMessage}>{message.content}</Text>
          </Box>
        </Box>
      </Box>
    );
  },
  (prev, next) => prev.message.content === next.message.content,
);

const AssistantMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    const prefix = "● ";
    const prefixWidth = prefix.length;

    // Use cached markdown renderer
    const renderedContent = useMemo(
      () => renderMarkdownCached(message.content),
      [message.content],
    );

    // Split by newlines to preserve line breaks in Ink
    const lines = useMemo(
      () => renderedContent.split("\n"),
      [renderedContent],
    );

    return (
      <Box flexDirection="column" marginBottom={1}>
        {lines.map((line, index) => (
          <Box key={index} flexDirection="row">
            <Box width={prefixWidth}>
              {index === 0 && (
                <Text color={TokyoNightTheme.assistantMessage}>{prefix}</Text>
              )}
            </Box>
            <Box flexGrow={1}>
              <Text color={TokyoNightTheme.assistantMessage}>{line}</Text>
            </Box>
          </Box>
        ))}
      </Box>
    );
  },
  (prev, next) => prev.message.content === next.message.content,
);

const ToolCallMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    if (!message.toolCall) return null;

    const { tool, input, output, metadata } = message.toolCall;
    const prefix = "● ";
    const prefixWidth = prefix.length;

    // Memoize expensive operations
    const argsPreview = useMemo(
      () => (input ? formatToolArgumentsSmart(input, 60) : ""),
      [input],
    );

    const richMetadata = useMemo(
      () => (metadata ? parseRichMetadata(metadata) : null),
      [metadata],
    );

    // Handle cancelled tool calls
    if (output === "[Cancelled]") {
      return (
        <Box flexDirection="column" marginBottom={1}>
          <Box flexDirection="row">
            <Box width={prefixWidth}>
              <Text color={TokyoNightTheme.toolName}>{prefix}</Text>
            </Box>
            <Box flexGrow={1}>
              <Text bold color={TokyoNightTheme.toolName}>
                {tool}
              </Text>
              <Text color={TokyoNightTheme.defaultText}> ({argsPreview})</Text>
            </Box>
          </Box>
          <Box>
            <Text color={TokyoNightTheme.warning} italic>
              {"  └─ Cancelled by user"}
            </Text>
          </Box>
        </Box>
      );
    }

    return (
      <Box flexDirection="column" marginBottom={1}>
        <Box flexDirection="row">
          <Box width={prefixWidth}>
            <Text color={TokyoNightTheme.toolName}>{prefix}</Text>
          </Box>
          <Box flexGrow={1}>
            <Text bold color={TokyoNightTheme.toolName}>
              {tool}
            </Text>
            <Text color={TokyoNightTheme.defaultText}> ({argsPreview})</Text>
          </Box>
        </Box>
        {output && (
          <Box>
            {richMetadata ? (
              <RichToolOutput metadata={richMetadata} plainOutput={output} />
            ) : (
              <Text color={TokyoNightTheme.toolResult}>
                {"  └─ "}
                {output.length > 200 ? output.slice(0, 200) + "..." : output}
              </Text>
            )}
          </Box>
        )}
      </Box>
    );
  },
  (prev, next) => {
    // Comprehensive equality check including metadata
    if (!prev.message.toolCall || !next.message.toolCall) {
      return prev.message.toolCall === next.message.toolCall;
    }

    const prevTool = prev.message.toolCall;
    const nextTool = next.message.toolCall;

    return (
      prevTool.call_id === nextTool.call_id &&
      prevTool.output === nextTool.output &&
      prevTool.running === nextTool.running &&
      JSON.stringify(prevTool.metadata) === JSON.stringify(nextTool.metadata)
    );
  },
);
