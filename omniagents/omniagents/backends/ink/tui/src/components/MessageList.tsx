/**
 * Component to display conversation messages
 */

import React, { useMemo } from 'react';
import { Box, Text } from 'ink';
import type { Message } from '../hooks/useMessages.js';
import { TokyoNightTheme } from '../theme.js';
import { formatToolArgumentsSmart } from '../utils/formatArguments.js';
import { renderMarkdown } from '../utils/markdown.js';
import { parseRichMetadata } from '../types/richMetadata.js';
import { RichToolOutput } from '../utils/richToolOutput.js';

export interface MessageListProps {
  messages: Message[];
}

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
  // Only re-render if tool call details changed
  if (!prev.message.toolCall || !next.message.toolCall) {
    return prev.message.toolCall === next.message.toolCall;
  }
  return prev.message.toolCall.call_id === next.message.toolCall.call_id &&
         prev.message.toolCall.output === next.message.toolCall.output &&
         prev.message.toolCall.running === next.message.toolCall.running;
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
  // Render assistant messages with markdown support (like Bubbletea does with Glamour)
  // Memoize markdown rendering to avoid re-rendering on every update
  const renderedContent = useMemo(() => renderMarkdown(message.content), [message.content]);

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box>
        <Text color={TokyoNightTheme.assistantMessage}>{'● '}</Text>
        <Text>{renderedContent}</Text>
      </Box>
    </Box>
  );
}, (prev, next) => prev.message.content === next.message.content);

export const MessageList = React.memo<MessageListProps>(({ messages }) => {
  if (messages.length === 0) {
    return null;
  }

  return (
    <Box flexDirection="column" paddingY={1}>
      {messages.map((message, index) => {
        // Create stable key using role, index, and timestamp if available
        // Use call_id for tool calls, timestamp for others
        const key = message.toolCall?.call_id
          || (message.timestamp ? `${message.role}-${message.timestamp.getTime()}`
          : `${message.role}-${index}`);

        switch (message.role) {
          case 'user':
            return <UserMessage key={key} message={message} />;
          case 'assistant':
            return <AssistantMessage key={key} message={message} />;
          case 'tool_call':
            return <ToolCallMessage key={key} message={message} />;
          case 'approval_request':
            // Approval requests are shown in ApprovalDialog, not in message history (like BubbleTea)
            return null;
          default:
            return null;
        }
      })}
    </Box>
  );
}, (prev, next) => {
  // Only re-render if message array length changed
  // Individual messages are memoized with their own comparison functions
  return prev.messages.length === next.messages.length;
});
