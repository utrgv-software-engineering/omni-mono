/**
 * Optimized message management hook with batching
 * Uses atomic store and event batching to prevent render cascades
 */

import { useEffect, useRef } from "react";
import { RPCClient } from "../rpc/client.js";
import { useActions, useAppStore } from "../store/appStore.js";
import { EventBatcher } from "../utils/eventBatcher.js";
import { debugLog } from "../utils/debugLog.js";
import type {
  ApprovalInfo,
  ElicitationInfo,
  McpApprovalInfo,
  Message,
} from "../store/appStore.js";
import { registerEscalationResolver } from "./escalationBridge.js";
import type { ElicitationAnswer } from "../components/ElicitationDialog.js";

export interface UseMessagesOptions {
  client: RPCClient | null;
  sessionId?: string;
  rpcReady?: boolean;
}

export function useMessagesOptimized(options: UseMessagesOptions) {
  const { client, sessionId, rpcReady } = options;
  const actions = useActions();
  const batcher = useRef(new EventBatcher()).current;
  const approvalQueueRef = useRef<ApprovalInfo[]>([]);
  const currentApprovalCallIdRef = useRef<string | null>(null);
  // Hosted-MCP approvals queue/state — independent from function-tool
  // approvals; multiple prompts can interleave but each surface is its
  // own queue so we don't conflate request_id and call_id.
  const mcpApprovalQueueRef = useRef<McpApprovalInfo[]>([]);
  const currentMcpApprovalRequestIdRef = useRef<string | null>(null);
  // Structured user-elicitation prompts — single-slot display with a
  // queue behind it, mirroring the approval surfaces. Keyed by
  // elicitation_id; first responder across clients wins so the resolved
  // handler advances the queue when another tab answers.
  const elicitationQueueRef = useRef<ElicitationInfo[]>([]);
  const currentElicitationIdRef = useRef<string | null>(null);

  // Buffer for messages with superseded tracking
  const messageBufferRef = useRef<
    Array<{
      content: string;
      timestamp: Date;
      superseded: boolean;
    }>
  >([]);

  useEffect(() => {
    if (!client || !rpcReady) return;

    // Load history if we have a session ID, UNLESS App just minted this
    // id from a sendMessage response — in that case our local optimistic
    // state is more authoritative than the server's persisted view (which
    // typically hasn't recorded the user message yet) and we'd otherwise
    // clobber the optimistic addMessage with setMessages([]).
    const skipHistoryLoad =
      !!sessionId &&
      useAppStore.getState().actions.consumeMintedSession(sessionId);
    if (sessionId && !skipHistoryLoad) {
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
                  timestamp: msg.timestamp
                    ? new Date(msg.timestamp)
                    : undefined,
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
              } else if (
                msgAny.type === "message" &&
                Array.isArray(msgAny.content)
              ) {
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
          // Don't turn off thinking here - there might be an active run
          // Let the run_end event control the thinking state
          debugLog(`Session history loaded (not changing thinking state)`);
        })
        .catch(console.error);
      // Also seed the "Up next" panel for this session. queue_changed
      // notifications take over from here. Run in parallel; failure is
      // non-fatal — the panel just stays empty until the next broadcast.
      client
        .listQueue(sessionId)
        .then((snap) => actions.setQueuedMessages(snap.items as any))
        .catch(() => actions.setQueuedMessages([]));
    } else if (!sessionId) {
      // No active session — clear any leftover queue state from a prior one.
      actions.setQueuedMessages([]);
    }

    // Helper to mark buffered messages as superseded (don't add to conversation)
    const markBufferAsSuperseded = () => {
      for (const msg of messageBufferRef.current) {
        msg.superseded = true;
      }
      if (messageBufferRef.current.length > 0) {
        debugLog(
          `Marked ${messageBufferRef.current.length} buffered messages as superseded`,
        );
      }
    };

    // MESSAGE OUTPUT - buffer and show in preamble
    const handleMessageOutput = (params: { content: string }) => {
      const timestamp = Date.now();
      debugLog(`message_output received at ${timestamp}, updating preamble`);
      batcher.add(() => {
        debugLog(
          `message_output batch executing at ${Date.now()}, delay: ${Date.now() - timestamp}ms`,
        );

        // Buffer this message as "not superseded yet"
        messageBufferRef.current.push({
          content: params.content,
          timestamp: new Date(),
          superseded: false,
        });

        // Show/update preamble
        actions.setPreamble(params.content);

        // Clear any retry/error status once we see forward progress
        actions.setThinking(true, null);

        debugLog(
          `Preamble updated, ${messageBufferRef.current.length} messages buffered`,
        );
      });
    };

    // Tools that opt out of transcript rendering. Their tool_called /
    // tool_result events still flow through (session history sees them,
    // the LLM sees them) but we don't push a message row. Today this is
    // only ``goal_complete`` — a structured signal to stop the /goal
    // autopilot loop, surfaced via the docked goal panel instead of as
    // a transcript row. Add new names here when introducing other
    // "control" tools that should be silent in the chat log.
    //
    // Tools whose name matches a registered client function are also
    // auto-hidden — they're UI dispatches (notify, escalate, etc.) and
    // the registered handler paints the user-visible side-effect, so
    // the transcript row would be redundant.
    const HIDDEN_TOOLS = new Set(["goal_complete"]);
    const isHiddenTool = (toolName: string) =>
      HIDDEN_TOOLS.has(toolName) || client.hasClientFunction(toolName);

    // TOOL CALLED - marks buffer as superseded but KEEPS preamble visible
    const handleToolCalled = (params: {
      tool: string;
      input: string;
      call_id: string;
    }) => {
      if (isHiddenTool(params.tool)) {
        // Don't render — handleToolResult also bails for the matching
        // call_id, so no half-rendered row is left behind.
        return;
      }
      batcher.add(() => {
        // Previous message(s) were intermediate thinking - mark as superseded
        markBufferAsSuperseded();

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

        // Preamble stays visible! Don't call setPreamble(null)
        // Clear any retry/error status once we see forward progress
        actions.setThinking(true, null);
      });
    };

    // TOOL RESULT - marks buffer as superseded but keeps preamble
    const handleToolResult = (params: {
      tool: string;
      output: string;
      call_id: string;
      metadata?: Record<string, unknown>;
    }) => {
      // Belt-and-suspenders: skip on both name (no tool_call message to
      // update) and metadata.display_type === "hidden" (catches tools
      // that aren't in HIDDEN_TOOLS yet but still asked to be silent).
      if (
        isHiddenTool(params.tool) ||
        params.metadata?.display_type === "hidden"
      ) {
        return;
      }
      batcher.add(() => {
        // Mark previous messages as superseded
        markBufferAsSuperseded();

        actions.updateToolResult(
          params.call_id,
          params.output,
          params.metadata,
        );

        // Side-channel: any tool can attach a tasks_snapshot to its
        // ui_metadata to refresh the docked task list above the input.
        // Today only task_create/task_list/task_get/task_update do.
        const snapshot = params.metadata?.tasks_snapshot;
        if (Array.isArray(snapshot)) {
          actions.setTasks(snapshot as any);
        }
        // Same pattern: bash tools attach bash_jobs_snapshot to refresh
        // the docked background-jobs panel.
        const jobsSnapshot = params.metadata?.bash_jobs_snapshot;
        if (Array.isArray(jobsSnapshot)) {
          actions.setBashJobs(jobsSnapshot as any);
        }

        // Preamble stays visible!
      });
    };

    // RUN STARTED - clear buffer and preamble
    const handleRunStarted = (params: {
      run_id: string;
      session_id: string;
      prompt?: string;
    }) => {
      const timestamp = Date.now();
      debugLog(
        `run_started received at ${timestamp}, clearing preamble and buffer`,
      );
      batcher.add(() => {
        debugLog(
          `run_started batch executing at ${Date.now()}, delay: ${Date.now() - timestamp}ms`,
        );

        // Clear buffer and preamble for new run
        messageBufferRef.current = [];
        actions.setPreamble(null);
        actions.setCurrentRunId(params.run_id);
        actions.setThinking(true, null);
        // A recap is a "while you were away" artifact — stale the moment
        // the user re-engages. Clear it when a new run starts. (The idle
        // auto-recap runs via a throwaway Runner.run, not the service run
        // path, so it never emits run_started here — this only clears on
        // genuine user activity.)
        actions.setRecap(null);

        // Queue-drainer / background-automation path: the server started
        // the run without a local sendMessage call, so no user message
        // has been pushed to the transcript yet. Append it from
        // params.prompt unless the last user message already matches
        // (idempotency for the direct-submit case where handleSubmit
        // already optimistic-added it).
        const promptFromEvent =
          typeof params.prompt === "string" ? params.prompt : "";
        if (promptFromEvent) {
          const messages = useAppStore.getState().messages;
          const lastUser = [...messages]
            .reverse()
            .find((m) => m.role === "user");
          if (!lastUser || lastUser.content !== promptFromEvent) {
            actions.addMessage({
              role: "user",
              content: promptFromEvent,
              timestamp: new Date(),
            });
          }
        }
      });
    };

    // QUEUE CHANGED - replace local queue snapshot
    const handleQueueChanged = (params: {
      session_id: string;
      depth: number;
      items: any[];
    }) => {
      // Session filter: only accept events for the active session so a
      // stale broadcast from a session we just switched away from doesn't
      // poison the "Up next" dock.
      const liveSessionId = useAppStore.getState().sessionId;
      if (
        params.session_id &&
        liveSessionId &&
        params.session_id !== liveSessionId
      ) {
        return;
      }
      batcher.add(() => {
        actions.setQueuedMessages(
          Array.isArray(params.items) ? params.items : [],
        );
      });
    };

    // RUN END - flush non-superseded messages and hide preamble
    const handleRunEnd = (params: {
      run_id: string;
      end_reason: string;
      error?: {
        message: string;
        code?: string;
        details?: unknown;
      };
    }) => {
      const timestamp = Date.now();
      debugLog(
        `run_end received at ${timestamp}, end_reason=${params.end_reason}, processing ${messageBufferRef.current.length} buffered messages`,
      );
      batcher.add(() => {
        debugLog(
          `run_end batch executing at ${Date.now()}, delay: ${Date.now() - timestamp}ms`,
        );

        const current = useAppStore.getState().currentRunId;
        if (current && params.run_id && params.run_id !== current) {
          debugLog(
            `Ignoring run_end for unrelated run_id=${params.run_id} (current=${current})`,
          );
          return;
        }

        // Only add non-superseded messages to conversation
        const nonSuperseded = messageBufferRef.current.filter(
          (msg) => !msg.superseded,
        );
        debugLog(
          `Adding ${nonSuperseded.length} non-superseded messages to conversation (${messageBufferRef.current.length - nonSuperseded.length} were superseded)`,
        );

        for (const bufferedMsg of nonSuperseded) {
          actions.addMessage({
            role: "assistant",
            content: bufferedMsg.content,
            timestamp: bufferedMsg.timestamp,
          });
        }

        // Clear buffer and hide preamble
        messageBufferRef.current = [];
        actions.setPreamble(null);

        // Check if run ended with an error
        if (params.end_reason.toLowerCase() === "error" && params.error) {
          // Show error as status with fallback text
          const raw = (params.error.message || "").trim();
          const fallbackType = (params as any).error?.type as
            | string
            | undefined;
          const msg = raw || fallbackType || "Unknown error";
          actions.setThinking(false, `Error: ${msg}`);
        } else {
          // Normal completion - clear everything
          actions.setThinking(false, null);
        }

        actions.setCurrentRunId(null);
      });
    };

    // RUN STATUS - not batched (rare event, needs immediate UI update)
    const handleRunStatus = (params: {
      run_id: string;
      status: string;
      message: string;
      attempt: number;
      total: number;
      next_delay?: number;
      type?: "info" | "warning" | "error" | "success" | "progress";
    }) => {
      const current = useAppStore.getState().currentRunId;
      if (current && params.run_id && params.run_id !== current) {
        debugLog(
          `Ignoring run_status for unrelated run_id=${params.run_id} (current=${current})`,
        );
        return;
      }
      // Build retry status message like bubbletea does
      let statusMessage: string;
      if (params.total > 0 && params.attempt > 0) {
        statusMessage = `Retrying ${params.attempt}/${params.total}: ${params.message}`;
      } else {
        statusMessage = `Retrying: ${params.message}`;
      }

      debugLog(`run_status: ${statusMessage}`);

      const t = params.type ?? "progress";
      actions.setStatus(statusMessage, t, true);
    };

    // TOKEN USAGE - batched
    const handleTokenUsage = (params: {
      delta: {
        input_tokens: number;
        output_tokens: number;
        total_tokens: number;
      };
      totals: {
        input_tokens: number;
        output_tokens: number;
        total_tokens: number;
      };
      model?: string;
      max_input_tokens?: number;
      max_output_tokens?: number;
    }) => {
      batcher.add(() => {
        actions.setUsage({
          inputTokens: params.totals.input_tokens,
          outputTokens: params.totals.output_tokens,
          totalTokens: params.totals.total_tokens,
          model: params.model,
          maxInputTokens: params.max_input_tokens,
          maxOutputTokens: params.max_output_tokens,
        });
      });
    };

    // TOOL APPROVAL - SDK 0.16 interruption flow.
    //
    // The server emits a tool_approval_requested event per pending tool
    // call. We surface the prompt immediately; the user response is sent
    // back via the tool_approval_response RPC method (see approveToolCall
    // below). When another channel (e.g. voice client) approves first, the
    // server broadcasts tool_approval_resolved so we dismiss the prompt.
    const handleToolApprovalRequested = (params: Record<string, unknown>) => {
      const approvalInfo: ApprovalInfo = {
        call_id: (params.call_id as string) || "",
        tool: (params.tool_name as string) || "",
        arguments: (params.arguments as string) || "{}",
        metadata: params.metadata as Record<string, unknown>,
        session_id: params.session_id as string,
        run_id: params.run_id as string,
      };
      if (!approvalInfo.call_id) return;

      approvalQueueRef.current.push(approvalInfo);

      if (!currentApprovalCallIdRef.current) {
        const next = approvalQueueRef.current.shift();
        if (next) {
          currentApprovalCallIdRef.current = next.call_id;
          actions.setApproval(next);
        }
      }
    };

    const handleToolApprovalResolved = (params: Record<string, unknown>) => {
      const callId = (params.call_id as string) || "";
      if (!callId) return;
      // Drop any queued prompt for this call_id (resolved by another tab)
      approvalQueueRef.current = approvalQueueRef.current.filter(
        (a) => a.call_id !== callId,
      );
      if (currentApprovalCallIdRef.current === callId) {
        const next = approvalQueueRef.current.shift() || null;
        currentApprovalCallIdRef.current = next ? next.call_id : null;
        actions.setApproval(next);
      }
    };

    // MCP APPROVAL — parallel to tool approval but keyed by request_id
    // (from the model's McpApprovalRequest) and identifies the hosted MCP
    // server via server_label. No "always" affordance.
    const handleMcpApprovalRequested = (params: Record<string, unknown>) => {
      const approvalInfo: McpApprovalInfo = {
        request_id: (params.request_id as string) || "",
        server_label: (params.server_label as string) || "",
        tool: (params.tool_name as string) || "",
        arguments: (params.arguments as string) || "{}",
        session_id: params.session_id as string,
        run_id: params.run_id as string,
      };
      if (!approvalInfo.request_id) return;

      mcpApprovalQueueRef.current.push(approvalInfo);

      if (!currentMcpApprovalRequestIdRef.current) {
        const next = mcpApprovalQueueRef.current.shift();
        if (next) {
          currentMcpApprovalRequestIdRef.current = next.request_id;
          actions.setMcpApproval(next);
        }
      }
    };

    const handleMcpApprovalResolved = (params: Record<string, unknown>) => {
      const requestId = (params.request_id as string) || "";
      if (!requestId) return;
      mcpApprovalQueueRef.current = mcpApprovalQueueRef.current.filter(
        (a) => a.request_id !== requestId,
      );
      if (currentMcpApprovalRequestIdRef.current === requestId) {
        const next = mcpApprovalQueueRef.current.shift() || null;
        currentMcpApprovalRequestIdRef.current = next ? next.request_id : null;
        actions.setMcpApproval(next);
      }
    };

    // USER ELICITATION — structured input prompts (protocol.md § "User
    // Elicitation Flow"). Same single-slot + queue shape as approvals.
    const handleElicitationRequested = (params: Record<string, unknown>) => {
      const info: ElicitationInfo = {
        elicitation_id: (params.elicitation_id as string) || "",
        kind: (params.kind as string) || "",
        message: (params.message as string) || "",
        title: params.title as string | undefined,
        input_schema: params.input_schema as
          | Record<string, unknown>
          | undefined,
        options: params.options as unknown[] | undefined,
        url: params.url as string | undefined,
        session_id: params.session_id as string | undefined,
        run_id: params.run_id as string | undefined,
        expires_at: params.expires_at as string | undefined,
      };
      if (!info.elicitation_id) return;

      elicitationQueueRef.current.push(info);

      if (!currentElicitationIdRef.current) {
        const next = elicitationQueueRef.current.shift();
        if (next) {
          currentElicitationIdRef.current = next.elicitation_id;
          actions.setElicitation(next);
        }
      }
    };

    const handleElicitationResolved = (params: Record<string, unknown>) => {
      const elicitationId = (params.elicitation_id as string) || "";
      if (!elicitationId) return;
      // Drop any queued prompt for this id (resolved by another client,
      // expired, or cleared by run end).
      elicitationQueueRef.current = elicitationQueueRef.current.filter(
        (e) => e.elicitation_id !== elicitationId,
      );
      if (currentElicitationIdRef.current === elicitationId) {
        const next = elicitationQueueRef.current.shift() || null;
        currentElicitationIdRef.current = next ? next.elicitation_id : null;
        actions.setElicitation(next);
      }
    };

    // CUSTOM STATUS - NOT batched (needs immediate UI update)
    const handleSetStatus = async (args: Record<string, unknown>) => {
      const text = args.text as string;
      const newStatus = text && text.trim() !== "" ? text : null;
      const type = (args as { type?: string }).type as
        | "info"
        | "warning"
        | "error"
        | "success"
        | "progress"
        | undefined;
      const showSpinnerArg = (args as { show_spinner?: boolean }).show_spinner;
      const showSpinner =
        typeof showSpinnerArg === "boolean" ? showSpinnerArg : true;
      actions.setStatus(newStatus, type ?? null, showSpinner);
      return { ack: true };
    };

    const handleSetSessionState = async (args: Record<string, unknown>) => {
      actions.setSessionState({
        model: (args.model as string | undefined) ?? null,
        label: (args.label as string | undefined) ?? null,
        provider: (args.provider as string | undefined) ?? null,
        reasoning_effort: (args.reasoning_effort as string | undefined) ?? null,
      });
      return { ack: true };
    };

    // Server-pushed bash-jobs panel refresh (fired after a UI-driven kill
    // so other tabs and the Ink TUI itself stay in sync — broadcast to
    // every channel on the session).
    const handleBashJobsUpdate = async (args: Record<string, unknown>) => {
      const snap = (args as { snapshot?: unknown }).snapshot;
      if (Array.isArray(snap)) {
        actions.setBashJobs(snap as any);
      }
      return { ack: true };
    };

    // Server-pushed workers panel refresh. Fired on every worker spawn
    // and on every terminal state (completed/cancelled/error) from
    // omni-code's tools/worker_tools.py. Snapshot is the full per-session
    // workers list; replace state wholesale.
    const handleWorkersUpdate = async (args: Record<string, unknown>) => {
      const snap = (args as { snapshot?: unknown }).snapshot;
      if (Array.isArray(snap)) {
        actions.setWorkers(snap as any);
      }
      return { ack: true };
    };

    // Server-pushed goal panel refresh. The /goal autopilot loop fires
    // this on start, every turn boundary, retry, and stop. snapshot
    // null means "no goal set" and clears the panel.
    const handleGoalUpdate = async (args: Record<string, unknown>) => {
      const snap = (args as { snapshot?: unknown }).snapshot;
      if (snap === null || snap === undefined) {
        actions.setGoal(null);
      } else if (typeof snap === "object") {
        actions.setGoal(snap as any);
      }
      return { ack: true };
    };

    // Server-pushed wakeup panel refresh. The schedule_wakeup tool /
    // server_functions.wakeup tick loop fires this on start, every tick,
    // and on cancel/exhaustion. snapshot null means "no schedule active"
    // and clears the panel.
    const handleWakeupUpdate = async (args: Record<string, unknown>) => {
      const snap = (args as { snapshot?: unknown }).snapshot;
      if (snap === null || snap === undefined) {
        actions.setWakeup(null);
      } else if (typeof snap === "object") {
        actions.setWakeup(snap as any);
      }
      return { ack: true };
    };

    const handleLoopUpdate = async (args: Record<string, unknown>) => {
      const snap = (args as { snapshot?: unknown }).snapshot;
      if (Array.isArray(snap)) {
        actions.setLoops(snap as any);
      }
      return { ack: true };
    };

    // Agent called the ``notify`` builtin (fire-and-forget heads-up).
    // The tool itself is hidden from the transcript (see isHiddenTool);
    // we push the message into the docked notifications panel. The
    // panel accumulates between user submissions and clears when the
    // user starts the next run, so a user returning to a long-running
    // session sees every notification they missed at a glance.
    const handleNotify = async (args: Record<string, unknown>) => {
      const message = (args.message as string) ?? "";
      if (message) {
        actions.addNotification(message);
      }
      return { ack: true };
    };

    // Server-pushed session recap (from /recap or the idle auto-recap).
    // Single-slot — newest replaces any prior recap. Cleared on the next
    // run_started.
    const handleRecap = async (args: Record<string, unknown>) => {
      const text = (args.text as string) ?? "";
      if (text) {
        actions.setRecap({ text, timestamp: Date.now() });
      }
      return { ack: true };
    };

    // Agent called the ``escalate`` builtin (blocking — expects a reply
    // before the tool call returns). Surface the banner, park a resolver
    // keyed by request_id, and return a promise the App.tsx submit
    // handler resolves with the user's next message. While the promise
    // is pending the RPC client doesn't send client_response, so the
    // agent's tool call stays blocked. The builtin has a 30-min
    // server-side timeout as the safety net. The tool itself uses
    // display_type="hidden" — the banner + the user's reply (rendered
    // as a normal user message by the submit interceptor) cover the
    // user-visible side of the interaction.
    const handleEscalate = async (args: Record<string, unknown>) => {
      const message = (args.message as string) ?? "";
      const requestId = (args.request_id as string) ?? "";
      const sessionIdArg = args.session_id as string | undefined;
      const runIdArg = args.run_id as string | undefined;
      if (!requestId) {
        return { ok: false, error: "missing request_id" };
      }
      actions.setEscalation({
        request_id: requestId,
        message,
        session_id: sessionIdArg,
        run_id: runIdArg,
      });
      return new Promise<{ reply: string }>((resolve) => {
        registerEscalationResolver(requestId, resolve);
      });
    };

    // Register event handlers
    client.on("message_output", handleMessageOutput);
    client.on("tool_called", handleToolCalled);
    client.on("tool_result", handleToolResult);
    client.on("run_started", handleRunStarted);
    client.on("run_end", handleRunEnd);
    client.on("run_status", handleRunStatus);
    client.on("token", handleTokenUsage);
    client.on("tool_approval_requested", handleToolApprovalRequested);
    client.on("tool_approval_resolved", handleToolApprovalResolved);
    client.on("mcp_approval_requested", handleMcpApprovalRequested);
    client.on("mcp_approval_resolved", handleMcpApprovalResolved);
    client.on("elicitation_requested", handleElicitationRequested);
    client.on("elicitation_resolved", handleElicitationResolved);
    client.on("queue_changed", handleQueueChanged);

    // Register client functions
    client.registerClientFunction("ui.set_status", handleSetStatus);
    client.registerClientFunction(
      "ui.set_session_state",
      handleSetSessionState,
    );
    client.registerClientFunction("ui.bash_jobs.update", handleBashJobsUpdate);
    client.registerClientFunction("ui.workers.update", handleWorkersUpdate);
    client.registerClientFunction("ui.goal.update", handleGoalUpdate);
    client.registerClientFunction("ui.wakeup.update", handleWakeupUpdate);
    client.registerClientFunction("ui.loop.update", handleLoopUpdate);
    client.registerClientFunction("notify", handleNotify);
    client.registerClientFunction("ui.recap", handleRecap);
    client.registerClientFunction("escalate", handleEscalate);

    // Cleanup
    return () => {
      client.off("message_output", handleMessageOutput);
      client.off("tool_called", handleToolCalled);
      client.off("tool_result", handleToolResult);
      client.off("run_started", handleRunStarted);
      client.off("run_end", handleRunEnd);
      client.off("run_status", handleRunStatus);
      client.off("token", handleTokenUsage);
      client.off("tool_approval_requested", handleToolApprovalRequested);
      client.off("tool_approval_resolved", handleToolApprovalResolved);
      client.off("mcp_approval_requested", handleMcpApprovalRequested);
      client.off("mcp_approval_resolved", handleMcpApprovalResolved);
      client.off("elicitation_requested", handleElicitationRequested);
      client.off("elicitation_resolved", handleElicitationResolved);
      client.off("queue_changed", handleQueueChanged);

      // Cleanup registered client functions
      client.unregisterClientFunction("ui.set_status");
      client.unregisterClientFunction("ui.set_session_state");
      client.unregisterClientFunction("ui.bash_jobs.update");
      client.unregisterClientFunction("ui.workers.update");
      client.unregisterClientFunction("ui.goal.update");
      client.unregisterClientFunction("ui.wakeup.update");
      client.unregisterClientFunction("ui.loop.update");
      client.unregisterClientFunction("notify");
      client.unregisterClientFunction("ui.recap");
      client.unregisterClientFunction("escalate");
    };
  }, [client, sessionId, rpcReady, batcher]);

  // Return approval handler function — sends the user's decision over the
  // wire and advances to the next queued prompt, if any.
  const approveToolCall = async (approved: boolean, alwaysApprove: boolean) => {
    const currentCallId = currentApprovalCallIdRef.current;
    if (!currentCallId || !client) return;

    // When the user picks "always" for tool X, the server-side allow-set
    // only covers FUTURE tool calls — sibling approvals already in the
    // queue (typical for parallel calls like three execute_bash in one
    // turn) would still prompt. Drain matching siblings here and approve
    // them too, so the user really only answers once.
    const currentTool = useAppStore.getState().approval?.tool;
    let siblings: ApprovalInfo[] = [];
    if (approved && alwaysApprove && currentTool) {
      siblings = approvalQueueRef.current.filter((a) => a.tool === currentTool);
      approvalQueueRef.current = approvalQueueRef.current.filter(
        (a) => a.tool !== currentTool,
      );
    }

    const next = approvalQueueRef.current.shift() || null;
    currentApprovalCallIdRef.current = next ? next.call_id : null;

    // Clear approval and restore thinking state
    // The server will decide via events whether the run continues or stops
    batcher.add(() => {
      actions.setApproval(next);
      actions.setThinking(!next);
    });

    try {
      await client.toolApprovalResponse(
        currentCallId,
        approved ? "approve" : "reject",
        approved && alwaysApprove,
        approved ? undefined : "User rejected tool call",
      );
    } catch (err) {
      // Best-effort; if the response fails the server will eventually time
      // out the future or the run will be cancelled.
    }

    // Fire-and-forget approve for each sibling — they share the tool
    // name of the just-approved call so "always" intent covers them.
    // Failures here are non-fatal (server times out the call's future
    // if no response arrives).
    for (const sibling of siblings) {
      client
        .toolApprovalResponse(sibling.call_id, "approve", true)
        .catch(() => {});
    }
  };

  // Hosted-MCP approval handler — parallel to ``approveToolCall`` but
  // sends ``mcp_approval_response`` and intentionally does not accept
  // ``alwaysApprove`` (no SDK-level always-allow on this path).
  const approveMcpToolCall = async (approved: boolean) => {
    const currentRequestId = currentMcpApprovalRequestIdRef.current;
    if (!currentRequestId || !client) return;

    const next = mcpApprovalQueueRef.current.shift() || null;
    currentMcpApprovalRequestIdRef.current = next ? next.request_id : null;

    batcher.add(() => {
      actions.setMcpApproval(next);
      actions.setThinking(!next);
    });

    try {
      await client.mcpApprovalResponse(
        currentRequestId,
        approved ? "approve" : "reject",
        approved ? undefined : "User rejected MCP tool call",
      );
    } catch (err) {
      // Best-effort — server will eventually time out / cancel the run.
    }
  };

  // Elicitation responder — sends the answer for the currently displayed
  // prompt and advances to the next queued one. A typed rejection
  // (-32050 not-found / -32051 already-resolved: another client won the
  // race) is expected and non-fatal; the resolved broadcast has already
  // dismissed or will dismiss the prompt.
  const respondElicitation = async (answer: ElicitationAnswer) => {
    const currentId = currentElicitationIdRef.current;
    if (!currentId || !client) return;

    const next = elicitationQueueRef.current.shift() || null;
    currentElicitationIdRef.current = next ? next.elicitation_id : null;

    batcher.add(() => {
      actions.setElicitation(next);
      actions.setThinking(!next);
    });

    try {
      await client.elicitationResponse(
        currentId,
        answer.action,
        answer.value,
        answer.reason,
      );
    } catch (err) {
      // Best-effort: duplicates/expiry reject with typed errors and the
      // server's elicitation_resolved broadcast keeps state converged.
    }
  };

  const cancelRun = async (runId: string) => {
    if (!client || !runId) return;

    actions.cancelRun(runId);

    // Send stop_run RPC call
    try {
      await client.stopRun(runId);
    } catch (error) {
      const msg =
        error instanceof Error ? error.message : "Failed to cancel run";
      actions.setThinking(false, `Error: ${msg}`);
    }
  };

  return {
    approveToolCall,
    approveMcpToolCall,
    respondElicitation,
    cancelRun,
  };
}
