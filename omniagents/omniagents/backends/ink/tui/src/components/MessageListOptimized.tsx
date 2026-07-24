/**
 * Optimized message list.
 *
 * When mounted inside a <Scrollable> (the common case), uses VirtualizedList
 * to render only messages in the visible viewport. Off-viewport items become
 * a single spacer Box of the correct measured height so the parent
 * Scrollable's scroll geometry stays accurate.
 *
 * Visible "frozen" messages (not the tail, not a running tool call) are
 * wrapped in <StaticRender> so their Yoga layout is cached — re-renders of
 * the list don't re-run layout on every visible message.
 *
 * Falls back to a plain map when no viewport info is available (e.g. when
 * rendered outside a Scrollable, in tests, or before first measurement). The
 * fallback still uses StaticRender for per-item layout caching.
 */

import React, { useCallback, useMemo } from "react";
import { Box, Text, StaticRender, useStdout } from "ink";
import { useMessages } from "../store/appStore.js";
import type { Message } from "../store/appStore.js";
import { TokyoNightTheme } from "../theme.js";
import { formatToolArgumentsSmart } from "../utils/formatArguments.js";
import { renderMarkdownCached } from "../utils/markdownCache.js";
import { parseRichMetadata } from "../types/richMetadata.js";
import { RichToolOutput } from "../utils/richToolOutput.js";
import { VirtualizedList } from "./shared/VirtualizedList.js";
import { useScrollViewport } from "./Scrollable.js";

function messageKey(message: Message, index: number): string {
  const contentPreview = message.content
    ? message.content.slice(0, 20)
    : "empty";
  const role = message.role || "unknown";
  return (
    message.toolCall?.call_id ||
    (message.timestamp
      ? `${role}-${message.timestamp.getTime()}`
      : undefined) ||
    `${role}-${contentPreview}-${index}`
  );
}

export const MessageListOptimized = React.memo(() => {
  const messages = useMessages();
  const { stdout } = useStdout();
  const terminalWidth = stdout?.columns ?? 80;
  const viewport = useScrollViewport();

  const renderItem = useCallback(
    (message: Message, _index: number) => <MessageItem message={message} />,
    [],
  );

  const lastIndex = messages.length - 1;
  const isStaticItem = useCallback(
    (message: Message, index: number) => {
      const isTail = index === lastIndex;
      const isRunning = message.toolCall?.running === true;
      return !isTail && !isRunning;
    },
    [lastIndex],
  );

  if (messages.length === 0) {
    return null;
  }

  // Inter-message spacing is owned by MessageItem itself (paddingBottom={1}),
  // not by either render path's container. This keeps the virtualized and
  // fallback paths in lockstep and — crucially — keeps the spacing inside the
  // border-box that ink's ResizeObserver measures (getComputedHeight), so the
  // virtualized path's offsets and head/tail spacers stay accurate.
  if (viewport && viewport.innerHeight > 0) {
    return (
      <VirtualizedList<Message>
        data={messages}
        renderItem={renderItem}
        keyExtractor={messageKey}
        estimatedItemHeight={3}
        containerHeight={viewport.innerHeight}
        scrollTop={viewport.scrollTop}
        width={terminalWidth}
        isStaticItem={isStaticItem}
      />
    );
  }

  // Fallback: render everything, with per-item StaticRender for frozen items.
  // No container gap — each MessageItem carries its own trailing spacing.
  return (
    <Box flexDirection="column">
      {messages.map((message, index) => {
        const key = messageKey(message, index);
        const canFreeze = isStaticItem(message, index);

        if (!canFreeze) {
          return <MessageItem key={key} message={message} />;
        }

        return (
          <StaticRender
            key={key}
            width={terminalWidth}
            deps={[
              message.content,
              message.toolCall?.output,
              message.toolCall?.metadata,
              terminalWidth,
            ]}
          >
            {() => <MessageItem message={message} />}
          </StaticRender>
        );
      })}
    </Box>
  );
});

// Individual message memoized with deep equality check
const MessageItem = React.memo<{ message: Message }>(
  ({ message }) => {
    const body = (() => {
      switch (message.role) {
        case "user":
          return <UserMessage message={message} />;
        case "assistant":
          return <AssistantMessage message={message} />;
        case "tool_call":
          // Tools can opt out of the conversation log by setting
          // ui_metadata.hidden = true (e.g. task_* tools rendered by
          // the docked Tasks panel instead).
          if (message.toolCall?.metadata?.hidden === true) {
            return null;
          }
          return <ToolCallMessage message={message} />;
        case "approval_request":
          // Approval requests shown in ApprovalDialog, not in message history
          return null;
        default:
          return null;
      }
    })();

    // Hidden / non-rendered messages contribute no spacing — the gap lives
    // here (not in the list container) so both render paths space identically
    // and the trailing row is part of this item's measured height.
    if (body === null) {
      return null;
    }
    return (
      <Box flexDirection="column" paddingBottom={1}>
        {body}
      </Box>
    );
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

    // Split on newlines and render one row per line, matching
    // AssistantMessage. A single <Text> here would collapse multi-line
    // content against the prefix column and look like whitespace was
    // stripped on submit.
    const lines = useMemo(
      () => (message.content ?? "").split("\n"),
      [message.content],
    );

    return (
      <Box flexDirection="column">
        {lines.map((line, index) => (
          <Box key={index} flexDirection="row">
            <Box width={prefixWidth}>
              {index === 0 && (
                <Text color={TokyoNightTheme.messagePrompt}>{prefix}</Text>
              )}
            </Box>
            <Box flexGrow={1}>
              <Text color={TokyoNightTheme.userMessage}>{line}</Text>
            </Box>
          </Box>
        ))}
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
    const lines = useMemo(() => renderedContent.split("\n"), [renderedContent]);

    return (
      <Box flexDirection="column">
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
        <Box flexDirection="column">
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
      <Box flexDirection="column">
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
