/**
 * Main application component
 */

import React, { useState, useCallback } from 'react';
import { Box, Text, useInput } from 'ink';
import { useRPC } from '../hooks/useRPC.js';
import { useMessages } from '../hooks/useMessages.js';
import { StatusBar } from './StatusBar.js';
import { MessageList } from './MessageList.js';
import { ToolActivity } from './ToolActivity.js';
import { InputPrompt } from './InputPrompt.js';
import { ApprovalDialog } from './ApprovalDialog.js';
import { Help } from './Help.js';

export interface AppProps {
  serverUrl: string;
  agentName: string;
  token?: string;
  debug?: boolean;
  sessionId?: string;
  welcomeText?: string;
  initialMessage?: string;
}

export const App: React.FC<AppProps> = ({
  serverUrl,
  agentName,
  token,
  debug,
  sessionId: initialSessionId,
  welcomeText,
  initialMessage,
}) => {
  const [sessionId, setSessionId] = useState<string | undefined>(initialSessionId);
  const [messageHistory, setMessageHistory] = useState<string[]>([]);

  // Initialize RPC connection
  const { client, connected, connecting, error, sendMessage } = useRPC({
    url: serverUrl,
    token,
    debug,
  });

  // Manage messages
  const { messages, toolCalls, isThinking, customStatus, pendingApproval, usage, currentRunId, addMessage, approveToolCall, cancelRun } = useMessages({
    client,
    sessionId,
  });

  // Handle approval
  const handleApproval = useCallback(
    (value: 'yes' | 'always' | 'no') => {
      const approved = value !== 'no';
      const alwaysApprove = value === 'always';
      approveToolCall(approved, alwaysApprove);
    },
    [approveToolCall]
  );

  // Handle Escape key for cancellation (like Bubbletea)
  const handleEscape = useCallback(() => {
    // During approval: Deny the approval
    if (pendingApproval) {
      handleApproval('no');
    }
    // During execution: Cancel the run
    else if (isThinking && currentRunId) {
      cancelRun();
    }
    // Other states: Do nothing
  }, [pendingApproval, isThinking, currentRunId, handleApproval, cancelRun]);

  useInput((input, key) => {
    if (key.escape) {
      handleEscape();
    }
  });

  // Handle user input
  const handleSubmit = useCallback(
    async (message: string) => {
      if (!client || !connected) {
        return;
      }

      // Add user message to UI immediately
      addMessage('user', message);

      // Add to history (avoid duplicates)
      setMessageHistory(prev => {
        if (prev.length === 0 || prev[prev.length - 1] !== message) {
          return [...prev, message];
        }
        return prev;
      });

      try {
        // Send to server and get session ID back
        const response = await sendMessage(message, sessionId);
        if (!sessionId) {
          setSessionId(response.session_id);
        }
      } catch (err) {
        console.error('Failed to send message:', err);
      }
    },
    [client, connected, sendMessage, sessionId, addMessage]
  );

  // Send initial message if provided
  React.useEffect(() => {
    if (initialMessage && connected && !messages.length) {
      handleSubmit(initialMessage);
    }
  }, [initialMessage, connected, messages.length, handleSubmit]);

  // Show loading state
  if (connecting && !connected) {
    return (
      <Box flexDirection="column">
        <Text>Connecting to {agentName}...</Text>
      </Box>
    );
  }

  // Show error state
  if (error && !connected) {
    return (
      <Box flexDirection="column">
        <Text color="red">Failed to connect to server:</Text>
        <Text>{error.message}</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column">
      {/* Status bar - only show when no messages */}
      {messages.length === 0 && (
        <Box paddingX={1}>
          <StatusBar agentName={agentName} connected={connected} sessionId={sessionId} />
        </Box>
      )}

      {/* Welcome message */}
      {welcomeText && messages.length === 0 && (
        <Box paddingY={1} marginTop={1} paddingX={1}>
          <Text>{welcomeText}</Text>
        </Box>
      )}

      {/* Message history */}
      <Box flexGrow={1} flexDirection="column" paddingX={1}>
        <MessageList messages={messages} />
      </Box>

      {/* Tool activity - hide during approval to prevent flicker */}
      {!pendingApproval && (
        <Box paddingX={1}>
          <ToolActivity isThinking={isThinking} customStatus={customStatus} />
        </Box>
      )}

      {/* Approval dialog - shown above input when approval needed */}
      {pendingApproval && (
        <Box marginTop={1} paddingX={1}>
          <ApprovalDialog
            tool={pendingApproval.tool}
            arguments={pendingApproval.arguments}
            metadata={pendingApproval.metadata}
            onApprove={handleApproval}
          />
        </Box>
      )}

      {/* Input prompt - always rendered, disabled during approval/thinking */}
      <Box marginTop={1}>
        <InputPrompt
          onSubmit={handleSubmit}
          disabled={!connected || isThinking || !!pendingApproval}
          history={messageHistory}
        />
      </Box>

      {/* Help text below input */}
      <Box paddingX={1} paddingBottom={1}>
        <Help
          isThinking={isThinking}
          isPendingApproval={!!pendingApproval}
          connected={connected}
          usage={usage}
        />
      </Box>
    </Box>
  );
};
