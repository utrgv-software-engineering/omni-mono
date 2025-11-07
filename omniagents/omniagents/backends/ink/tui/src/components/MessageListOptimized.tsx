/**
 * Optimized message list with virtual scrolling
 * Alternate buffer eliminates need for Static component
 */

import React, { useMemo } from 'react';
import { Box, Text } from 'ink';
import { useMessages } from '../store/appStore.js';
import type { Message } from '../store/appStore.js';
import { TokyoNightTheme } from '../theme.js';
import { formatToolArgumentsSmart } from '../utils/formatArguments.js';
import { renderMarkdownCached } from '../utils/markdownCache.js';
import { parseRichMetadata } from '../types/richMetadata.js';
import { RichToolOutput } from '../utils/richToolOutput.js';

// Virtual scrolling: only render last N messages
const VISIBLE_MESSAGE_LIMIT = 50;

export const MessageListOptimized = React.memo(() => {
  const allMessages = useMessages();

  // Only show last N messages for performance
  const visibleMessages = useMemo(() => {
    const startIdx = Math.max(0, allMessages.length - VISIBLE_MESSAGE_LIMIT);
    return allMessages.slice(startIdx);
  }, [allMessages]);

  if (visibleMessages.length === 0) {
    return null;
  }

  return (
    <Box flexDirection="column">
      {visibleMessages.map((message, index) => {
        const key = message.toolCall?.call_id
          || (message.timestamp ? `${message.role}-${message.timestamp.getTime()}` : undefined)
          || `${message.role}-${message.content.slice(0, 20)}-${index}`;

        return <MessageItem key={key} message={message} />;
      })}
    </Box>
  );
});

// Individual message memoized with deep equality check
const MessageItem = React.memo<{ message: Message }>(({ message }) => {
  switch (message.role) {
    case 'user':
      return <UserMessage message={message} />;
    case 'assistant':
      return <AssistantMessage message={message} />;
    case 'tool_call':
      return <ToolCallMessage message={message} />;
    case 'approval_request':
      // Approval requests shown in ApprovalDialog, not in message history
      return null;
    default:
      return null;
  }
}, (prev, next) => {
  // Deep equality check - only re-render if content actually changed
  if (prev.message.role !== next.message.role) return false;
  if (prev.message.content !== next.message.content) return false;

  // For tool calls, check if output changed
  if (prev.message.toolCall && next.message.toolCall) {
    return prev.message.toolCall.output === next.message.toolCall.output &&
           prev.message.toolCall.running === next.message.toolCall.running &&
           prev.message.toolCall.metadata === next.message.toolCall.metadata;
  }

  return true;
});

const UserMessage = React.memo<{ message: Message }>(({ message }) => (
  <Box flexDirection="column" marginBottom={1}>
    <Box>
      <Text color={TokyoNightTheme.messagePrompt}>{'> '}</Text>
      <Text color={TokyoNightTheme.userMessage}>{message.content}</Text>
    </Box>
  </Box>
), (prev, next) => prev.message.content === next.message.content);

const AssistantMessage = React.memo<{ message: Message }>(({ message }) => {
  // Use cached markdown renderer
  const renderedContent = useMemo(
    () => renderMarkdownCached(message.content),
    [message.content]
  );

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box>
        <Text color={TokyoNightTheme.assistantMessage}>{'● '}</Text>
        <Text>{renderedContent}</Text>
      </Box>
    </Box>
  );
}, (prev, next) => prev.message.content === next.message.content);

const ToolCallMessage = React.memo<{ message: Message }>(({ message }) => {
  if (!message.toolCall) return null;

  const { tool, input, output, metadata } = message.toolCall;

  // Memoize expensive operations
  const argsPreview = useMemo(
    () => input ? formatToolArgumentsSmart(input, 60) : '',
    [input]
  );

  const richMetadata = useMemo(
    () => metadata ? parseRichMetadata(metadata) : null,
    [metadata]
  );

  // Handle cancelled tool calls
  if (output === '[Cancelled]') {
    return (
      <Box flexDirection="column" marginBottom={1}>
        <Box>
          <Text color={TokyoNightTheme.toolName}>{'● '}</Text>
          <Text bold color={TokyoNightTheme.toolName}>{tool}</Text>
          <Text dimColor>({argsPreview})</Text>
        </Box>
        <Box>
          <Text color={TokyoNightTheme.warning} italic>  └─ Cancelled by user</Text>
        </Box>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box>
        <Text color={TokyoNightTheme.toolName}>{'● '}</Text>
        <Text bold color={TokyoNightTheme.toolName}>{tool}</Text>
        <Text dimColor>({argsPreview})</Text>
      </Box>
      {output && (
        <Box>
          {richMetadata ? (
            <RichToolOutput metadata={richMetadata} plainOutput={output} />
          ) : (
            <Text color={TokyoNightTheme.toolResult}>
              {'  └─ '}{output.length > 200 ? output.slice(0, 200) + '...' : output}
            </Text>
          )}
        </Box>
      )}
    </Box>
  );
}, (prev, next) => {
  // Comprehensive equality check including metadata
  if (!prev.message.toolCall || !next.message.toolCall) {
    return prev.message.toolCall === next.message.toolCall;
  }

  const prevTool = prev.message.toolCall;
  const nextTool = next.message.toolCall;

  return prevTool.call_id === nextTool.call_id &&
         prevTool.output === nextTool.output &&
         prevTool.running === nextTool.running &&
         JSON.stringify(prevTool.metadata) === JSON.stringify(nextTool.metadata);
});
