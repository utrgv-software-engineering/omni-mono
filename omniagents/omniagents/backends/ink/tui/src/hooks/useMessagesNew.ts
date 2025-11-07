/**
 * Optimized message management hook with batching
 * Uses atomic store and event batching to prevent render cascades
 */

import { useEffect, useRef } from "react";
import { RPCClient } from "../rpc/client.js";
import { useActions } from "../store/appStore.js";
import { EventBatcher } from "../utils/eventBatcher.js";
import type { ApprovalInfo, Message } from "../store/appStore.js";

export interface UseMessagesOptions {
  client: RPCClient | null;
  sessionId?: string;
}

export function useMessagesOptimized(options: UseMessagesOptions) {
  const { client, sessionId } = options;
  const actions = useActions();
  const batcher = useRef(new EventBatcher()).current;
  const approvalResolverRef = useRef<
    | ((value: {
        approved: boolean;
        always_approve?: boolean;
        error_message?: string;
      }) => void)
    | null
  >(null);

  useEffect(() => {
    if (!client) return;

    // Load history if we have a session ID
    if (sessionId) {
      client
        .getSessionHistory(sessionId)
        .then((history) => {
          const msgs: Message[] = [];
          const toolCallMap = new Map<string, Message>();

          for (const msg of history) {
            const msgAny = msg as any;

            // Handle tool calls (type: "function_call")
            if (msgAny.type === "function_call") {
              const toolMsg: Message = {
                role: "tool_call",
                content: "",
                timestamp: msg.timestamp ? new Date(msg.timestamp) : undefined,
                toolCall: {
                  tool: msgAny.name || "",
                  input: msgAny.arguments || "{}",
                  call_id: msgAny.call_id || "",
                  running: false,
                  metadata: msgAny.metadata,
                },
              };
              msgs.push(toolMsg);
              if (msgAny.call_id) {
                toolCallMap.set(msgAny.call_id, toolMsg);
              }
            }
            // Handle tool outputs (type: "function_call_output")
            else if (msgAny.type === "function_call_output") {
              const callId = msgAny.call_id || "";
              const existingMsg = toolCallMap.get(callId);
              if (existingMsg && existingMsg.toolCall) {
                // Update existing tool call with output and metadata
                existingMsg.toolCall.output = msgAny.output || "";
                if (msgAny.metadata) {
                  existingMsg.toolCall.metadata = msgAny.metadata;
                }
              } else {
                // Create a standalone message if no matching call found
                const toolMsg: Message = {
                  role: "tool_call",
                  content: "",
                  timestamp: msg.timestamp ? new Date(msg.timestamp) : undefined,
                  toolCall: {
                    tool: "unknown",
                    input: "{}",
                    call_id: callId,
                    output: msgAny.output || "",
                    running: false,
                    metadata: msgAny.metadata,
                  },
                };
                msgs.push(toolMsg);
              }
            }
            // Handle regular messages (user/assistant with content field)
            else if (msg.role === "user" || msg.role === "assistant") {
              // Extract content from both simple and complex message formats
              let content = "";
              if (typeof msg.content === "string") {
                content = msg.content;
              } else if (msgAny.type === "message" && Array.isArray(msgAny.content)) {
                // OpenAI message format: extract text from content array
                for (const part of msgAny.content) {
                  if (part.type === "output_text" && part.text) {
                    content += part.text;
                  }
                }
              } else {
                content = JSON.stringify(msg.content);
              }

              msgs.push({
                role: msg.role as "user" | "assistant",
                content,
                timestamp: msg.timestamp ? new Date(msg.timestamp) : undefined,
              });
            }
          }

          actions.setMessages(msgs);
        })
        .catch(console.error);
    }

    // MESSAGE OUTPUT - batched
    const handleMessageOutput = (params: { content: string }) => {
      batcher.add(() => {
        actions.addMessage({
          role: "assistant",
          content: params.content,
          timestamp: new Date(),
        });
        actions.setThinking(false, null);
      });
    };

    // TOOL CALLED - batched
    const handleToolCalled = (params: {
      tool: string;
      input: string;
      call_id: string;
    }) => {
      batcher.add(() => {
        actions.addMessage({
          role: "tool_call",
          content: "",
          timestamp: new Date(),
          toolCall: {
            tool: params.tool,
            input: params.input,
            call_id: params.call_id,
            running: true,
          },
        });
        actions.setThinking(true);
      });
    };

    // TOOL RESULT - batched
    const handleToolResult = (params: {
      tool: string;
      output: string;
      call_id: string;
      metadata?: Record<string, unknown>;
    }) => {
      batcher.add(() => {
        actions.updateToolResult(
          params.call_id,
          params.output,
          params.metadata,
        );
      });
    };

    // RUN STARTED - batched
    const handleRunStarted = (params: {
      run_id: string;
      session_id: string;
    }) => {
      batcher.add(() => {
        actions.setCurrentRunId(params.run_id);
        actions.setThinking(true);
      });
    };

    // RUN END - batched
    const handleRunEnd = () => {
      batcher.add(() => {
        actions.setThinking(false, null);
        actions.setCurrentRunId(null);
      });
    };

    // RUN STATUS - not batched (rare event)
    const handleRunStatus = (params: { status: string; message?: string }) => {
      if (params.message) {
        console.error("[Status]", params.status, params.message);
      }
    };

    // TOKEN USAGE - batched
    const handleTokenUsage = (params: {
      totals: {
        input_tokens: number;
        output_tokens: number;
        total_tokens: number;
      };
      model?: string;
      context_window?: number;
    }) => {
      batcher.add(() => {
        actions.setUsage({
          inputTokens: params.totals.input_tokens,
          outputTokens: params.totals.output_tokens,
          totalTokens: params.totals.total_tokens,
          model: params.model,
          contextWindow: params.context_window,
        });
      });
    };

    // TOOL APPROVAL - immediate UI update (no batching, no extra message)
    const handleApproval = async (args: Record<string, unknown>) => {
      const approvalInfo: ApprovalInfo = {
        approval_id: (args.approval_id as string) || "",
        request_id: (args.request_id as string) || "",
        tool: (args.tool as string) || "",
        arguments: (args.arguments as string) || "{}",
        metadata: args.metadata as Record<string, unknown>,
        session_id: args.session_id as string,
        run_id: args.run_id as string,
      };

      // Set pending approval - single state update, no extra message
      actions.setApproval(approvalInfo);

      // Return promise that waits for user to approve/deny
      return new Promise<{
        approved: boolean;
        always_approve?: boolean;
        error_message?: string;
      }>((resolve) => {
        approvalResolverRef.current = resolve;
      });
    };

    // CUSTOM STATUS - NOT batched (needs immediate UI update)
    const handleSetStatus = async (args: Record<string, unknown>) => {
      const text = args.text as string;
      const newStatus = text && text.trim() !== "" ? text : null;
      actions.setThinking(true, newStatus);
      return { ack: true };
    };

    // Register event handlers
    client.on("message_output", handleMessageOutput);
    client.on("tool_called", handleToolCalled);
    client.on("tool_result", handleToolResult);
    client.on("run_started", handleRunStarted);
    client.on("run_end", handleRunEnd);
    client.on("run_status", handleRunStatus);
    client.on("token", handleTokenUsage);

    // Register client functions
    client.registerClientFunction("ui.request_tool_approval", handleApproval);
    client.registerClientFunction("ui.set_status", handleSetStatus);

    // Cleanup
    return () => {
      client.off("message_output", handleMessageOutput);
      client.off("tool_called", handleToolCalled);
      client.off("tool_result", handleToolResult);
      client.off("run_started", handleRunStarted);
      client.off("run_end", handleRunEnd);
      client.off("run_status", handleRunStatus);
      client.off("token", handleTokenUsage);

      // Cleanup registered client functions
      client.unregisterClientFunction("ui.request_tool_approval");
      client.unregisterClientFunction("ui.set_status");
    };
  }, [client, sessionId, actions, batcher]);

  // Return approval handler function
  const approveToolCall = async (approved: boolean, alwaysApprove: boolean) => {
    if (!approvalResolverRef.current) return;

    // Clear approval and restore thinking state
    // The server will decide via events whether the run continues or stops
    batcher.add(() => {
      actions.setApproval(null);
      actions.setThinking(true); // Always go back to thinking - let server send run_end if it stops
    });

    // Resolve the promise that the server is waiting for
    const result = approved
      ? { approved: true, always_approve: alwaysApprove }
      : {
          approved: false,
          always_approve: false,
          error_message: "User rejected tool call",
        };

    approvalResolverRef.current(result);
    approvalResolverRef.current = null;
  };

  const cancelRun = async (runId: string) => {
    if (!client || !runId) return;

    actions.cancelRun(runId);

    // Send stop_run RPC call
    try {
      await client.stopRun(runId);
    } catch (error) {
      console.error("Failed to cancel run:", error);
    }
  };

  return {
    approveToolCall,
    cancelRun,
  };
}
