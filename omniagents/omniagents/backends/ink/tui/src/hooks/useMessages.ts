/**
 * React hook for managing conversation messages
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { RPCClient } from '../rpc/client.js';

export interface ToolCallInfo {
  tool: string;
  input: string; // JSON string of arguments
  call_id: string;
  output?: string;
  metadata?: Record<string, unknown>;
  running: boolean;
}

export interface ApprovalInfo {
  approval_id: string;
  request_id: string;
  tool: string;
  arguments: string;
  metadata?: Record<string, unknown>;
  session_id?: string;
  run_id?: string;
}

export interface Message {
  role: 'user' | 'assistant' | 'system' | 'tool_call' | 'approval_request';
  content: string;
  timestamp?: Date;
  toolCall?: ToolCallInfo;
  approval?: ApprovalInfo;
}

// Legacy - for backward compat with ToolActivity
export interface ToolCall {
  tool: string;
  input: string;
  call_id: string;
}

export interface UseMessagesOptions {
  client: RPCClient | null;
  sessionId?: string;
}

export interface UsageInfo {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  model?: string;
  contextWindow?: number;
}

export interface UseMessagesResult {
  messages: Message[];
  toolCalls: ToolCall[];
  isThinking: boolean;
  customStatus: string | null;
  pendingApproval: ApprovalInfo | null;
  usage: UsageInfo | null;
  currentRunId: string | null;
  addMessage: (role: 'user' | 'assistant', content: string) => void;
  clearMessages: () => void;
  approveToolCall: (approved: boolean, alwaysApprove: boolean) => Promise<void>;
  cancelRun: () => Promise<void>;
}

/**
 * Hook for managing conversation messages and events
 */
export function useMessages(options: UseMessagesOptions): UseMessagesResult {
  const { client, sessionId } = options;
  const [messages, setMessages] = useState<Message[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [customStatus, setCustomStatus] = useState<string | null>(null);
  const [pendingApproval, setPendingApproval] = useState<ApprovalInfo | null>(null);
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const approvalResolveRef = useRef<((value: { approved: boolean; always_approve?: boolean; error_message?: string }) => void) | null>(null);

  useEffect(() => {
    if (!client) return;

    // Load history if we have a session ID
    if (sessionId) {
      client
        .getSessionHistory(sessionId)
        .then((history) => {
          const msgs = history.map((msg) => ({
            role: msg.role as 'user' | 'assistant',
            content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
            timestamp: msg.timestamp ? new Date(msg.timestamp) : undefined,
          }));
          setMessages(msgs);
        })
        .catch(console.error);
    }

    // Listen for message output events
    const handleMessageOutput = (params: { content: string }) => {
      const message: Message = {
        role: 'assistant', // Server messages are always from assistant
        content: params.content,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, message]);
      setIsThinking(prev => prev ? false : prev);
      setCustomStatus(prev => prev ? null : prev);
    };

    // Listen for tool calls
    const handleToolCalled = (params: {
      tool: string;
      input: string; // JSON string
      call_id: string;
    }) => {
      // Add tool call as a message
      const toolMessage: Message = {
        role: 'tool_call',
        content: '',
        timestamp: new Date(),
        toolCall: {
          tool: params.tool,
          input: params.input,
          call_id: params.call_id,
          running: true,
        },
      };
      setMessages((prev) => [...prev, toolMessage]);

      // Also add to active tool calls for the ToolActivity component
      setToolCalls((prev) => [...prev, params]);
      setIsThinking(prev => prev ? prev : true);
    };

    // Listen for tool results
    const handleToolResult = (params: { tool: string; output: string; call_id: string; metadata?: Record<string, unknown> }) => {
      // Find the tool call message and update it with results
      setMessages((prev) => prev.map((msg) => {
        if (msg.role === 'tool_call' && msg.toolCall?.call_id === params.call_id) {
          return {
            ...msg,
            toolCall: {
              ...msg.toolCall,
              output: params.output,
              metadata: params.metadata,
              running: false,
            },
          };
        }
        return msg;
      }));

      // Remove from active tool calls after a brief moment
      setTimeout(() => {
        setToolCalls((prev) => prev.filter((call) => call.call_id !== params.call_id));
      }, 500);
    };

    // Listen for run started - this gives us the run_id we need for cancellation
    const handleRunStarted = (params: { run_id: string; session_id: string }) => {
      setCurrentRunId(prev => prev === params.run_id ? prev : params.run_id);
      setIsThinking(prev => prev ? prev : true);
    };

    // Listen for run end
    const handleRunEnd = () => {
      setIsThinking(prev => prev ? false : prev);
      setToolCalls(prev => prev.length > 0 ? [] : prev);
      setCurrentRunId(prev => prev ? null : prev);
      setCustomStatus(prev => prev ? null : prev);
    };

    // Listen for run status
    const handleRunStatus = (params: { status: string; message?: string }) => {
      if (params.message) {
        // Could show status messages in UI
        console.error('[Status]', params.status, params.message);
      }
    };

    // Listen for token usage updates
    const handleTokenUsage = (params: {
      totals: { input_tokens: number; output_tokens: number; total_tokens: number };
      model?: string;
      context_window?: number;
    }) => {
      setUsage({
        inputTokens: params.totals.input_tokens,
        outputTokens: params.totals.output_tokens,
        totalTokens: params.totals.total_tokens,
        model: params.model,
        contextWindow: params.context_window,
      });
    };

    // Register client function for tool approval
    // This returns a Promise that will be resolved when user approves/denies
    client.registerClientFunction('ui.request_tool_approval', async (args: Record<string, unknown>) => {
      const approvalInfo: ApprovalInfo = {
        approval_id: args.approval_id as string || '',
        request_id: args.request_id as string || '',
        tool: args.tool as string || '',
        arguments: args.arguments as string || '{}',
        metadata: args.metadata as Record<string, unknown>,
        session_id: args.session_id as string,
        run_id: args.run_id as string,
      };

      // Set pending approval - this will show the dialog
      setPendingApproval(approvalInfo);

      // Add approval request as a message
      const approvalMessage: Message = {
        role: 'approval_request',
        content: '',
        timestamp: new Date(),
        approval: approvalInfo,
      };
      setMessages((prev) => [...prev, approvalMessage]);

      // Return a promise that waits for user to approve/deny
      return new Promise<{ approved: boolean; always_approve?: boolean; error_message?: string }>((resolve) => {
        approvalResolveRef.current = resolve;
      });
    });

    // Register client function for custom tool status
    // Tools can set custom status text instead of generic "Thinking..."
    client.registerClientFunction('ui.set_status', async (args: Record<string, unknown>) => {
      const text = args.text as string;
      // Empty text means clear the status
      const newStatus = text && text.trim() !== '' ? text : null;
      // Only update if the value actually changed to prevent unnecessary re-renders
      setCustomStatus(prev => prev === newStatus ? prev : newStatus);
      return { ack: true };
    });

    client.on('message_output', handleMessageOutput);
    client.on('tool_called', handleToolCalled);
    client.on('tool_result', handleToolResult);
    client.on('run_started', handleRunStarted);
    client.on('run_end', handleRunEnd);
    client.on('run_status', handleRunStatus);
    client.on('token', handleTokenUsage);

    return () => {
      client.off('message_output', handleMessageOutput);
      client.off('tool_called', handleToolCalled);
      client.off('tool_result', handleToolResult);
      client.off('run_started', handleRunStarted);
      client.off('run_end', handleRunEnd);
      client.off('run_status', handleRunStatus);
      client.off('token', handleTokenUsage);
    };
  }, [client, sessionId]);

  const addMessage = useCallback((role: 'user' | 'assistant', content: string) => {
    const message: Message = {
      role,
      content,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, message]);

    if (role === 'user') {
      setIsThinking(true);
    }
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setToolCalls([]);
    setIsThinking(false);
  }, []);

  const approveToolCall = useCallback(async (approved: boolean, alwaysApprove: boolean) => {
    if (!pendingApproval || !approvalResolveRef.current) return;

    // Remove approval message from history (like BubbleTea does)
    setMessages((prev) => prev.filter((msg) => msg.role !== 'approval_request'));

    // Clear pending approval
    setPendingApproval(null);

    // Resolve the promise that the server is waiting for
    const result = approved
      ? { approved: true, always_approve: alwaysApprove }
      : { approved: false, always_approve: false, error_message: 'User rejected tool call' };

    approvalResolveRef.current(result);
    approvalResolveRef.current = null;
  }, [pendingApproval]);

  const cancelRun = useCallback(async () => {
    if (!client || !currentRunId) return;

    // Mark any running tool calls as cancelled (like BubbleTea does)
    setMessages((prev) => prev.map((msg) => {
      if (msg.role === 'tool_call' && msg.toolCall?.running) {
        return {
          ...msg,
          toolCall: {
            ...msg.toolCall,
            output: '[Cancelled]',
            running: false,
          },
        };
      }
      return msg;
    }));

    // Clear thinking state and custom status (only if different to prevent flicker)
    setIsThinking(prev => prev ? false : prev);
    setToolCalls(prev => prev.length > 0 ? [] : prev);
    setCustomStatus(prev => prev ? null : prev);

    // Add cancellation message
    setMessages((prev) => [...prev, {
      role: 'assistant',
      content: '[Run cancelled by user]',
      timestamp: new Date(),
    }]);

    // Send stop_run RPC call
    const runIdToCancel = currentRunId;
    setCurrentRunId(null); // Clear immediately to prevent double cancellation

    try {
      await client.stopRun(runIdToCancel);
    } catch (error) {
      console.error('Failed to cancel run:', error);
    }
  }, [client, currentRunId]);

  return {
    messages,
    toolCalls,
    isThinking,
    customStatus,
    pendingApproval,
    usage,
    currentRunId,
    addMessage,
    clearMessages,
    approveToolCall,
    cancelRun,
  };
}
