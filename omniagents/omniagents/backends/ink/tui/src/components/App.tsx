/**
 * Optimized main application component
 * Uses atomic state management and optimized components
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import { useRPC } from '../hooks/useRPC.js';
import { useMessagesOptimized } from '../hooks/useMessagesNew.js';
import { useMessages, useThinking, useApproval, useUsage, useSessionId, useCurrentRunId, useActions } from '../store/appStore.js';
import type { ApprovalInfo } from '../store/appStore.js';
import { StatusBar } from './StatusBar.js';
import { MessageListOptimized } from './MessageListOptimized.js';
import { ToolActivityOptimized } from './ToolActivityOptimized.js';
import { MultiLineInputOptimized } from './MultiLineInputOptimized.js';
import { ApprovalDialog } from './ApprovalDialog.js';
import { Help } from './Help.js';

/**
 * Wrapper to keep ApprovalDialog mounted to prevent flicker
 */
const ApprovalDialogWrapper = React.memo<{
  approval: ApprovalInfo | null;
  onApprove: (value: 'yes' | 'always' | 'no') => void;
}>(({ approval, onApprove }) => {
  if (!approval) {
    // Render empty placeholder to keep component mounted
    return null;
  }

  return (
    <ApprovalDialog
      tool={approval.tool}
      arguments={approval.arguments}
      metadata={approval.metadata}
      onApprove={onApprove}
    />
  );
}, (prev, next) => {
  // Only re-render if approval actually changed
  if (prev.approval === next.approval) return true;
  if (!prev.approval || !next.approval) return false;
  return prev.approval.request_id === next.approval.request_id;
});

/**
 * Isolate MessageList subscription to prevent parent re-renders
 */
const MessageListWrapper = React.memo(() => {
  return (
    <Box flexGrow={1} flexDirection="column" paddingX={1}>
      <MessageListOptimized />
    </Box>
  );
});

/**
 * Isolate ToolActivity subscription to prevent parent re-renders
 */
const ToolActivityWrapper = React.memo<{
  approval: ApprovalInfo | null;
  thinking: boolean;
}>(({ approval, thinking }) => {
  return (
    <Box paddingX={1} display={!approval && thinking ? 'flex' : 'none'}>
      <ToolActivityOptimized />
    </Box>
  );
}, (prev, next) => {
  return prev.approval === next.approval && prev.thinking === next.thinking;
});

/**
 * Isolate Help subscription to prevent parent re-renders
 */
const HelpWrapper = React.memo<{
  connected: boolean;
  thinking: boolean;
  approval: ApprovalInfo | null;
  usage: ReturnType<typeof useUsage>;
  waitingForSecondCtrlC: boolean;
}>(({ connected, thinking, approval, usage, waitingForSecondCtrlC }) => {
  return (
    <Box paddingX={1} paddingBottom={1}>
      <Help
        isThinking={thinking}
        isPendingApproval={!!approval}
        connected={connected}
        usage={usage}
        waitingForSecondCtrlC={waitingForSecondCtrlC}
      />
    </Box>
  );
}, (prev, next) => {
  return prev.connected === next.connected &&
         prev.thinking === next.thinking &&
         prev.approval === next.approval &&
         prev.usage === next.usage &&
         prev.waitingForSecondCtrlC === next.waitingForSecondCtrlC;
});

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
  const [messageHistory, setMessageHistory] = useState<string[]>([]);

  // RPC connection
  const { client, connected, connecting, error, sendMessage } = useRPC({
    url: serverUrl,
    token,
    debug,
  });

  // Get state from atomic store
  const messages = useMessages();
  const thinking = useThinking();
  const approval = useApproval();
  const usage = useUsage();
  const sessionId = useSessionId();
  const currentRunId = useCurrentRunId();
  const actions = useActions();

  // Ctrl+C handling - require double tap to quit
  const { exit } = useApp();
  const ctrlCCount = useRef(0);
  const ctrlCTimer = useRef<NodeJS.Timeout | null>(null);
  const [waitingForSecondCtrlC, setWaitingForSecondCtrlC] = useState(false);

  // Setup message handlers with batching
  const { approveToolCall, cancelRun } = useMessagesOptimized({
    client,
    sessionId: initialSessionId,
  });

  // Set initial session ID
  useEffect(() => {
    if (initialSessionId) {
      actions.setSessionId(initialSessionId);
    }
  }, [initialSessionId, actions]);

  // Handle approval
  const handleApproval = useCallback(
    (value: 'yes' | 'always' | 'no') => {
      const approved = value !== 'no';
      const alwaysApprove = value === 'always';
      approveToolCall(approved, alwaysApprove);
    },
    [approveToolCall]
  );

  // Handle Escape key for cancellation (passed to child components to avoid parent re-renders)
  const handleEscape = useCallback(() => {
    if (approval) {
      // During approval: Deny
      handleApproval('no');
    } else if (thinking && currentRunId) {
      // During execution: Cancel
      cancelRun(currentRunId);
    }
  }, [approval, thinking, currentRunId, handleApproval, cancelRun]);

  // Handle user input
  const handleSubmit = useCallback(
    async (message: string) => {
      if (!client || !connected) {
        return;
      }

      // Add user message to UI immediately
      actions.addMessage({
        role: 'user',
        content: message,
        timestamp: new Date(),
      });

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
          actions.setSessionId(response.session_id);
        }
      } catch (err) {
        console.error('Failed to send message:', err);
      }
    },
    [client, connected, sendMessage, sessionId, actions]
  );

  // Handle Ctrl+C - double tap to quit (like bubbletea)
  useInput(
    useCallback(
      (input, key) => {
        if (key.ctrl && input === 'c') {
          ctrlCCount.current++;

          // Clear existing timer
          if (ctrlCTimer.current) {
            clearTimeout(ctrlCTimer.current);
          }

          if (ctrlCCount.current === 1) {
            // First Ctrl+C: Cancel current run if active
            if (thinking && currentRunId) {
              cancelRun(currentRunId);
            } else if (approval) {
              handleApproval('no');
            }

            // Show warning message
            setWaitingForSecondCtrlC(true);

            // Reset counter and hide warning after 1 second
            ctrlCTimer.current = setTimeout(() => {
              ctrlCCount.current = 0;
              setWaitingForSecondCtrlC(false);
            }, 1000);
          } else if (ctrlCCount.current >= 2) {
            // Second Ctrl+C within 1 second: Exit
            setWaitingForSecondCtrlC(false);
            exit();
          }
        }
      },
      [thinking, currentRunId, approval, cancelRun, handleApproval, exit]
    )
  );

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (ctrlCTimer.current) {
        clearTimeout(ctrlCTimer.current);
      }
    };
  }, []);

  // Send initial message if provided
  useEffect(() => {
    if (initialMessage && connected && messages.length === 0) {
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
      {/* Status bar - always visible */}
      <Box paddingX={1}>
        <StatusBar agentName={agentName} connected={connected} sessionId={sessionId} />
      </Box>

      {/* Welcome message - keep in DOM, control visibility */}
      {welcomeText && (
        <Box paddingY={1} marginTop={1} paddingX={1} display={messages.length === 0 ? 'flex' : 'none'}>
          <Text>{welcomeText}</Text>
        </Box>
      )}

      {/* Message history - dynamic height - isolated subscription */}
      <MessageListWrapper />

      {/* Tool activity - ALWAYS in DOM, just hidden - isolated subscription */}
      <ToolActivityWrapper approval={approval} thinking={thinking} />

      {/* Approval dialog - ALWAYS in DOM with placeholder to prevent mount/unmount */}
      <Box marginTop={1} paddingX={1} display={approval ? 'flex' : 'none'}>
        <ApprovalDialogWrapper
          approval={approval}
          onApprove={handleApproval}
        />
      </Box>

      {/* Input prompt - always rendered, disabled during approval/thinking */}
      <Box marginTop={1}>
        <MultiLineInputOptimized
          onSubmit={handleSubmit}
          onEscape={handleEscape}
          disabled={!connected || thinking || !!approval}
          history={messageHistory}
        />
      </Box>

      {/* Help text below input - isolated subscription */}
      <HelpWrapper
        connected={connected}
        thinking={thinking}
        approval={approval}
        usage={usage}
        waitingForSecondCtrlC={waitingForSecondCtrlC}
      />
    </Box>
  );
};
