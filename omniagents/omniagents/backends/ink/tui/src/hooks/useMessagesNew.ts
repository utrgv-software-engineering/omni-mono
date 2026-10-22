/**
 * Optimized message management hook with batching
 * Uses atomic store and event batching to prevent render cascades
 */

import { useEffect, useRef } from "react";
import { RPCClient } from "../rpc/client.js";
import { useActions, useAppStore } from "../store/appStore.js";
import { EventBatcher } from "../utils/eventBatcher.js";
import { debugLog } from "../utils/debugLog.js";
import {
  guardianReviewLine,
  parseGuardianReview,
  parseWorkflowReview,
  workflowReviewLine,
} from "../components/reviewer-model.js";
import type {
  ApprovalInfo,
  ElicitationInfo,
  McpApprovalInfo,
  Message,
} from "../store/appStore.js";
import {
  registerEscalationResolver,
  discardEscalation,
} from "./escalationBridge.js";
import type { ElicitationAnswer } from "../components/ElicitationDialog.js";
import { normalizeItems } from "../store/conversation.js";
import { planStepsToTasks } from "../components/task-model.js";
import type { TaskSummary } from "../store/appStore.js";
import type {
  ConversationItem,
  ConversationItemKind,
  ConversationItemStatus,
  ConversationRunDiff,
} from "../types/conversation.js";
import type {
  ClientRequestResolvedParams,
  ItemUpdatedParams,
} from "../protocol/generated/gui-v2.js";

export interface UseMessagesOptions {
  client: RPCClient | null;
  sessionId?: string;
  rpcReady?: boolean;
}

export function useMessagesOptimized(options: UseMessagesOptions) {
  const { client, sessionId, rpcReady } = options;
  const actions = useActions();
  const sharedBatcher = useRef(new EventBatcher()).current;
  const batcher = sharedBatcher;
  const responsesInFlight = useRef(new Set<string>());
  const activeSessionRef = useRef(sessionId);
  activeSessionRef.current = sessionId;
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

  useEffect(() => {
    if (!client || !rpcReady) return;
    client.setActiveSession?.(sessionId);
    let alive = true;
    let revision = 0;
    const current = () => alive && activeSessionRef.current === sessionId;
    const batcher = {
      add: (fn: () => void) =>
        sharedBatcher.add(() => {
          if (current()) fn();
        }),
    };
    const listeners: Array<[string, (params: any) => void]> = [];
    const listen = (event: string, handler: (params: any) => void) => {
      const guarded = (params: any) => {
        if (
          !current() ||
          (sessionId && params?.session_id && params.session_id !== sessionId)
        )
          return;
        revision++;
        handler(params);
      };
      listeners.push([event, guarded]);
      client.on(event as any, guarded);
    };
    const register = (
      name: string,
      handler: (args: Record<string, unknown>) => Promise<unknown>,
    ) => {
      client.registerClientFunction(name, async (args) => {
        if (
          !current() ||
          (sessionId && args.session_id && args.session_id !== sessionId)
        )
          return { ignored: true };
        return handler(args);
      });
    };
    const resetPrompts = () => {
      approvalQueueRef.current = [];
      mcpApprovalQueueRef.current = [];
      elicitationQueueRef.current = [];
      currentApprovalCallIdRef.current = null;
      currentMcpApprovalRequestIdRef.current = null;
      currentElicitationIdRef.current = null;
      actions.setApproval(null);
      actions.setMcpApproval(null);
      actions.setElicitation(null);
      actions.setEscalation(null);
    };
    const hasSnapshot =
      !!sessionId && typeof client.synchronizeSession === "function";
    const hydrationRevision = revision;

    // Load history if we have a session ID, UNLESS App just minted this
    // id from a sendMessage response — in that case our local optimistic
    // state is more authoritative than the server's persisted view (which
    // typically hasn't recorded the user message yet) and we'd otherwise
    // clobber the optimistic addMessage with setMessages([]).
    const skipHistoryLoad =
      !!sessionId &&
      useAppStore.getState().actions.consumeMintedSession(sessionId);
    if (!skipHistoryLoad) {
      resetPrompts();
      actions.setCurrentRunId(null);
      actions.setThinking(false, null);
    }
    // Legacy hydration: fold the raw session-history message list into the
    // store's Message shape. Only used when the canonical item read fails
    // (older server, or a session with no materialized thread).
    const loadLegacyHistory = () =>
      client
        .getSessionHistory(sessionId!)
        .then((history) => {
          if (!current() || revision !== hydrationRevision) return;
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

    if (sessionId && !skipHistoryLoad && !hasSnapshot) {
      // Canonical hydration: the persisted item model (thread_id ==
      // session_id). One paginated walk, then a pure projection — the
      // item's recorded status answers "still running?", no event folding.
      client
        .listAllItems(sessionId)
        .then((items) => {
          if (!current() || revision !== hydrationRevision) return;
          actions.setConversationItems(items);
          actions.setMessages(normalizeItems(items));
          debugLog(
            `Hydrated ${items.length} conversation items (not changing thinking state)`,
          );
        })
        .catch((err) => {
          // Older server or a session without a materialized thread —
          // fall back to the legacy history fold.
          debugLog(`list_items hydration failed, using legacy history: ${err}`);
          if (current()) void loadLegacyHistory();
        });
      // Seed the docked task list from the durable plan (when granted).
      // The tasks_snapshot tool side-channel and live `plan` item updates
      // take over from here; failure is non-fatal (dock stays as-is).
      if (client.isGranted("get_plan")) {
        client
          .getPlan(sessionId)
          .then((result) => {
            if (!current() || revision !== hydrationRevision) return;
            const tasks = planStepsToTasks(result.plan?.steps);
            if (tasks) actions.setTasks(tasks);
          })
          .catch(() => {});
      }
      // Also seed the "Up next" panel for this session. queue_changed
      // notifications take over from here. Run in parallel; failure is
      // non-fatal — the panel just stays empty until the next broadcast.
      client
        .listQueue(sessionId)
        .then((snap) => {
          if (current() && revision === hydrationRevision)
            actions.setQueuedMessages(snap.items as any);
        })
        .catch(() => {
          if (current() && revision === hydrationRevision)
            actions.setQueuedMessages([]);
        });
    } else if (!sessionId) {
      // No active session — clear any leftover queue state from a prior one.
      actions.setQueuedMessages([]);
    }

    // Apply a live task-list update. The plan lives in the dock; the
    // transcript says nothing about it.
    //
    // This used to also synthesize `◐ plan · X` / `✔ plan · X` system
    // messages by diffing the task list. That was a client-local
    // reconstruction of information the transcript already had and had
    // deliberately dropped: `task_create`/`task_update` are real TOOL_CALL
    // items (content.tool_kind == "plan"), and those tools mark themselves
    // `hidden` so the dock can own the display. Re-deriving prose from the
    // state those hidden calls produced fused glyph, separator and subject
    // into one string that became the de-facto schema, made plan beats
    // indistinguishable from compaction/approval/artifact system messages,
    // and — because the synthesis was gated on an active run while the
    // hidden tool calls stayed hidden on replay — meant a resumed session
    // rendered a different transcript than the live one.
    //
    // If plan progress should appear in the transcript again, it belongs to
    // the hidden tool calls that actually carry it (they replay, and ship a
    // tasks_snapshot), as a typed TranscriptRow — not as fabricated prose.
    const applyTasks = (tasks: TaskSummary[]) => {
      actions.setTasks(tasks);
    };

    // MESSAGE OUTPUT - the model's commentary joins the transcript as it
    // arrives, so the text between tool calls stays visible.
    const handleMessageOutput = (params: { content: string }) => {
      batcher.add(() => {
        if (params.content.trim()) {
          actions.addMessage({
            role: "assistant",
            content: params.content,
            timestamp: new Date(),
          });
        }
        // Clear any retry/error status once we see forward progress
        actions.setThinking(true, null);
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

    // TOOL CALLED
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

        // Clear any retry/error status once we see forward progress
        actions.setThinking(true, null);
      });
    };

    // TOOL RESULT
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
          applyTasks(snapshot as unknown as TaskSummary[]);
        }
        // Same pattern: bash tools attach bash_jobs_snapshot to refresh
        // the docked background-jobs panel.
        const jobsSnapshot = params.metadata?.bash_jobs_snapshot;
        if (Array.isArray(jobsSnapshot)) {
          actions.setBashJobs(jobsSnapshot as any);
        }
      });
    };

    // RUN STARTED
    const handleRunStarted = (params: {
      run_id: string;
      session_id: string;
      prompt?: string;
      prompt_role?: string;
    }) => {
      const timestamp = Date.now();
      debugLog(`run_started received at ${timestamp}`);
      batcher.add(() => {
        debugLog(
          `run_started batch executing at ${Date.now()}, delay: ${Date.now() - timestamp}ms`,
        );

        actions.setCurrentRunId(params.run_id);
        actions.setThinking(true, null);
        // The previous run's aggregated diff is stale once a new run
        // starts; the strip token clears with it.
        actions.setRunDiff(null);

        // Queue-drainer / background-automation path: the server started
        // the run without a local sendMessage call, so no user message
        // has been pushed to the transcript yet. Append it from
        // params.prompt unless the last user message already matches
        // (idempotency for the direct-submit case where handleSubmit
        // already optimistic-added it). ``prompt_role`` is honored:
        // notification batches (worker completions, wakeups) arrive as
        // "assistant" and render as the agent observing its background
        // activity — NOT as fake user input. Matches hydration, which
        // restores these turns with their history role.
        const promptFromEvent =
          typeof params.prompt === "string" ? params.prompt : "";
        if (promptFromEvent) {
          if (params.prompt_role === "assistant") {
            actions.addMessage({
              role: "assistant",
              content: promptFromEvent,
              timestamp: new Date(),
            });
          } else {
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

    // ITEM UPDATED — the durable record of the conversation advancing
    // (experimental; only delivered when granted at the handshake).
    // Three consumers:
    //   plan items     → refresh the docked task list (with the
    //                    blocked_by edges the tool snapshot also carries);
    //   run_diff items → refresh the aggregated-changes panel live;
    //   everything else → merge into the item cache; while no local run
    //                    is streaming (another client's run, background
    //                    automation, finalization sweeps) also re-derive
    //                    the transcript from the cache. During a local
    //                    run the event stream owns `messages`, and the
    //                    cache catches back up on the next hydration.
    const handleItemUpdated = (params: ItemUpdatedParams) => {
      const liveSessionId = useAppStore.getState().sessionId;
      if (
        params.session_id &&
        liveSessionId &&
        params.session_id !== liveSessionId
      ) {
        return;
      }
      const content = params.content ?? {};
      const item: ConversationItem = {
        item_id: params.item_id,
        thread_id: params.thread_id,
        turn_id: params.turn_id ?? null,
        seq: params.item_seq,
        kind: params.kind as ConversationItemKind,
        status: params.status as ConversationItemStatus,
        role: null,
        created_at: params.updated_at ?? 0,
        updated_at: params.updated_at ?? 0,
        completed_at: null,
        revision: params.revision,
        content,
        source_ref: {},
        long_lived: false,
        source: "recorded",
        schema_version: 1,
      };
      if (!item.item_id) return;

      batcher.add(() => {
        actions.mergeConversationItems([item]);
        if (item.kind === "plan") {
          const tasks = planStepsToTasks(content.steps);
          if (tasks) applyTasks(tasks);
          return;
        }
        if (item.kind === "run_diff") {
          if (
            typeof content.diff === "string" &&
            Array.isArray(content.files)
          ) {
            actions.setRunDiff(content as unknown as ConversationRunDiff);
          }
          return;
        }
        if (!useAppStore.getState().currentRunId) {
          actions.setMessages(
            normalizeItems(useAppStore.getState().conversationItems),
          );
        }
      });
    };

    // CLIENT REQUEST RESOLVED — another client answered a pending
    // client_request (today: the blocking ``escalate``). Drop the parked
    // resolver so no stale client_response is ever sent, and dismiss the
    // banner if it is the one on screen.
    const handleClientRequestResolved = (
      params: ClientRequestResolvedParams,
    ) => {
      const requestId = params.request_id;
      if (!requestId) return;
      discardEscalation(requestId);
      const escalation = useAppStore.getState().escalation;
      if (escalation?.request_id === requestId) {
        actions.setEscalation(null);
      }
    };

    // RUN END
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
        `run_end received at ${timestamp}, end_reason=${params.end_reason}`,
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

      // Aggregated changes: the net diff the run is responsible for.
      // The live `run_diff` item_updated usually lands the store value
      // first; this fetch is the safety net. On top of the strip token,
      // the run leaves one transcript line recording what it changed.
      if (sessionId && client.isGranted("get_run_diff")) {
        const requestRevision = revision;
        client
          .getRunDiff(sessionId)
          .then((result) => {
            if (!current() || revision !== requestRevision) return;
            const activeRun = useAppStore.getState().currentRunId;
            if (activeRun && activeRun !== params.run_id) return;
            const diff = result.run_diff;
            if (!diff) return;
            actions.setRunDiff(diff);
            const files = diff.stats.files_changed;
            actions.addMessage({
              role: "system",
              content: `${files} file${files === 1 ? "" : "s"} edited +${diff.stats.additions} −${diff.stats.deletions}`,
              timestamp: new Date(),
              runDiff: diff,
            });
          })
          .catch(() => {});
      }
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

      if (
        currentApprovalCallIdRef.current === approvalInfo.call_id ||
        approvalQueueRef.current.some((a) => a.call_id === approvalInfo.call_id)
      )
        return;
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

      if (
        currentMcpApprovalRequestIdRef.current === approvalInfo.request_id ||
        mcpApprovalQueueRef.current.some(
          (a) => a.request_id === approvalInfo.request_id,
        )
      )
        return;
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

      if (
        currentElicitationIdRef.current === info.elicitation_id ||
        elicitationQueueRef.current.some(
          (a) => a.elicitation_id === info.elicitation_id,
        )
      )
        return;
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
    const handleSubagentsUpdate = async (args: Record<string, unknown>) => {
      const snap = (args as { snapshot?: unknown }).snapshot;
      if (Array.isArray(snap)) {
        actions.setSubagents(snap as any);
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
    // Lands in the transcript as agent prose — it IS retrospective
    // content, and history is where retrospectives live. (It used to pin
    // a ~36-row panel between the user and the input.)
    const handleRecap = async (args: Record<string, unknown>) => {
      const text = (args.text as string) ?? "";
      if (text) {
        actions.addMessage({
          role: "assistant",
          content: `**Session recap**\n\n${text}`,
          timestamp: new Date(),
        });
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

    // Live worker mini-transcript relay: one narrative event from a
    // worker's run (tool_called / message_output / run lifecycle),
    // tagged with the worker id. Buffered per worker in the store; the
    // Agents screen renders the buffer as a live activity feed.
    const handleSubagentEvent = async (args: Record<string, unknown>) => {
      const workerId = args.subagent_id as string;
      const method = args.method as string;
      if (workerId && method) {
        actions.addSubagentEvent(workerId, {
          method,
          params: (args.params as Record<string, unknown>) ?? {},
        });
      }
      return { ack: true };
    };

    // Register event handlers
    // A reviewer settled a decision the TUI would otherwise have
    // prompted for. On this path the server never broadcasts
    // ``tool_approval_requested`` at all — service.py is explicit that
    // the response "never reached the client" — so without this handler
    // the tool is simply approved or denied with nothing shown.
    const handleToolApprovalReviewed = (params: unknown) => {
      const review = parseGuardianReview(params);
      if (!review) return;
      actions.addMessage({
        role: "review",
        content: guardianReviewLine(review),
        review: {
          kind: "guardian",
          // A denial stopped a tool call; an allow is the ordinary case
          // and folds into the beat span rather than costing a row.
          blocking: review.outcome === "deny",
          line: guardianReviewLine(review),
          rationale: review.rationale,
        },
        timestamp: new Date(),
      });
    };

    // A plan-step completion was reviewed against its exit criteria.
    // ``workflow.completion_reviewer`` DEFAULTS to guardian, so these run
    // whether or not the switch is exposed — and a rejection writes
    // nothing to the plan, it just hands the rationale back to the model.
    // Unrendered, that reads as a step that silently will not stick.
    const handlePlanCompletionReviewed = (params: unknown) => {
      const review = parseWorkflowReview(params);
      if (!review) return;
      actions.addMessage({
        role: "review",
        content: workflowReviewLine(review),
        review: {
          kind: "workflow",
          // Only a rejection stopped the step. The other outcomes are
          // trust signals about a completion that stood.
          blocking: review.outcome === "reject",
          line: workflowReviewLine(review),
          rationale: review.rationale,
        },
        timestamp: new Date(),
      });
    };

    listen("message_output", handleMessageOutput);
    listen("tool_called", handleToolCalled);
    listen("tool_result", handleToolResult);
    listen("run_started", handleRunStarted);
    listen("run_end", handleRunEnd);
    listen("run_status", handleRunStatus);
    listen("token", handleTokenUsage);
    listen("tool_approval_requested", handleToolApprovalRequested);
    listen("tool_approval_resolved", handleToolApprovalResolved);
    listen("mcp_approval_requested", handleMcpApprovalRequested);
    listen("mcp_approval_resolved", handleMcpApprovalResolved);
    listen("elicitation_requested", handleElicitationRequested);
    listen("elicitation_resolved", handleElicitationResolved);
    listen("queue_changed", handleQueueChanged);
    listen("item_updated", handleItemUpdated);
    listen("client_request_resolved", handleClientRequestResolved);
    listen("tool_approval_reviewed", handleToolApprovalReviewed);
    listen("plan_completion_reviewed", handlePlanCompletionReviewed);

    // Agent emitted an artifact (``display_artifact`` / the artifact
    // bridge). Upserting is the contract: re-issuing the same
    // ``artifact_id`` means "here is the new version of that artifact".
    //
    // NOTE the payload is ``{title, content, mode, artifact_id}`` — NOT
    // the shape ``types/protocol.ts`` used to declare. Content for
    // image/pdf modes is a base64 data URL, which is exactly why nothing
    // here touches the transcript: the drawer decides what is safe to
    // print, and a megabyte of base64 is not.
    const handleAddArtifact = async (args: Record<string, unknown>) => {
      const title = typeof args.title === "string" ? args.title : "";
      const content = typeof args.content === "string" ? args.content : "";
      const mode = typeof args.mode === "string" ? args.mode : "markdown";
      const artifactId =
        typeof args.artifact_id === "string" && args.artifact_id !== ""
          ? args.artifact_id
          : undefined;
      const eventSessionId =
        typeof args.session_id === "string" ? args.session_id : undefined;
      actions.addArtifact({
        artifactId,
        title,
        content,
        mode,
        sessionId: eventSessionId,
        updatedAt: Date.now(),
        revision: 0,
      });
      return { ack: true };
    };

    // Register client functions
    register("ui.set_status", handleSetStatus);
    register("ui.set_session_state", handleSetSessionState);
    register("ui.bash_jobs.update", handleBashJobsUpdate);
    register("ui.subagents.update", handleSubagentsUpdate);
    register("ui.subagent.event", handleSubagentEvent);
    register("ui.goal.update", handleGoalUpdate);
    register("ui.wakeup.update", handleWakeupUpdate);
    register("ui.loop.update", handleLoopUpdate);
    register("notify", handleNotify);
    register("ui.recap", handleRecap);
    register("ui.add_artifact", handleAddArtifact);
    register("escalate", handleEscalate);

    let recovering = false;
    const resync = (id: string) => {
      if (!current() || id !== sessionId || recovering) return;
      recovering = true;
      void client
        .synchronizeSession(
          id,
          (result) => {
            if (!current()) return;
            resetPrompts();
            // A parked escalation keeps its resolver while another session is
            // selected. Restore the banner without executing the function twice.
            for (const request of result.snapshot.pending_requests ?? []) {
              const requestId = String(request.request_id ?? "");
              if (
                request.function === "escalate" &&
                requestId &&
                !client.hasQueuedClientResponse?.(requestId)
              ) {
                actions.setEscalation({
                  request_id: requestId,
                  message: String(
                    (request.args as Record<string, unknown>)?.message ?? "",
                  ),
                  session_id: id,
                  run_id: request.run_id as string | undefined,
                });
              }
            }
            const items = result.snapshot.items;
            actions.setConversationItems(items);
            actions.setMessages(normalizeItems(items));
            actions.setQueuedMessages(result.snapshot.queue as any);
            actions.setCurrentRunId(
              result.run_active ? result.active_run_id : null,
            );
            actions.setThinking(result.run_active, null);
            actions.setTasks([]);
            actions.setRunDiff(null);
            for (const item of items) {
              if (item.kind === "plan") {
                const tasks = planStepsToTasks(item.content.steps);
                if (tasks) actions.setTasks(tasks);
              }
              if (item.kind === "run_diff")
                actions.setRunDiff(
                  item.content as unknown as ConversationRunDiff,
                );
            }
          },
          current,
        )
        .catch((error) => {
          if (current())
            actions.setStatus(
              `Session recovery failed: ${String(error)}`,
              "error",
              false,
            );
        })
        .finally(() => {
          recovering = false;
        });
    };
    client.onResyncRequired = resync;
    listen("client_response_failed", (error) =>
      actions.setStatus(
        `Response delivery failed: ${String(error)}`,
        "error",
        false,
      ),
    );
    if (hasSnapshot && !skipHistoryLoad) resync(sessionId!);

    // Cleanup
    return () => {
      alive = false;
      for (const [event, handler] of listeners) client.off(event, handler);
      if (client.onResyncRequired === resync)
        client.onResyncRequired = () => {};

      // Cleanup registered client functions
      client.unregisterClientFunction("ui.add_artifact");
      client.unregisterClientFunction("ui.set_status");
      client.unregisterClientFunction("ui.set_session_state");
      client.unregisterClientFunction("ui.bash_jobs.update");
      client.unregisterClientFunction("ui.subagents.update");
      client.unregisterClientFunction("ui.subagent.event");
      client.unregisterClientFunction("ui.goal.update");
      client.unregisterClientFunction("ui.wakeup.update");
      client.unregisterClientFunction("ui.loop.update");
      client.unregisterClientFunction("notify");
      client.unregisterClientFunction("ui.recap");
      client.unregisterClientFunction("escalate");
    };
  }, [client, sessionId, rpcReady, batcher]);

  // Keep the control visible until delivery is acknowledged. A failed send can
  // be retried by the user with the same correlation id (server-idempotent).
  const respond = async (
    id: string,
    send: () => Promise<unknown>,
    done: () => void,
  ) => {
    if (responsesInFlight.current.has(id)) return false;
    responsesInFlight.current.add(id);
    const selected = activeSessionRef.current;
    try {
      await send();
      if (selected !== activeSessionRef.current) return false;
      done();
      return true;
    } catch (error) {
      const stillPending =
        currentApprovalCallIdRef.current === id ||
        currentMcpApprovalRequestIdRef.current === id ||
        currentElicitationIdRef.current === id;
      if (selected === activeSessionRef.current && stillPending) {
        actions.setStatus(
          `Could not send response. Please retry: ${String(error)}`,
          "error",
          false,
        );
      }
      return false;
    } finally {
      responsesInFlight.current.delete(id);
    }
  };

  const approveToolCall = async (
    approved: boolean,
    alwaysApprove: boolean,
    expectedId?: string,
  ) => {
    const id = currentApprovalCallIdRef.current;
    if (!id || !client || (expectedId !== undefined && expectedId !== id))
      return;
    const selectedSession = activeSessionRef.current;
    const tool = useAppStore.getState().approval?.tool;
    const siblings =
      approved && alwaysApprove
        ? approvalQueueRef.current.filter((a) => a.tool === tool)
        : [];
    const advance = (callId: string) => {
      approvalQueueRef.current = approvalQueueRef.current.filter(
        (a) => a.call_id !== callId,
      );
      if (currentApprovalCallIdRef.current !== callId) return;
      const next = approvalQueueRef.current.shift() ?? null;
      currentApprovalCallIdRef.current = next?.call_id ?? null;
      actions.setApproval(next);
    };
    const sent = await respond(
      id,
      () =>
        client.toolApprovalResponse(
          id,
          approved ? "approve" : "reject",
          approved && alwaysApprove,
          approved ? undefined : "User rejected tool call",
        ),
      () => advance(id),
    );
    if (!sent) return;
    for (const sibling of siblings) {
      if (activeSessionRef.current !== selectedSession) return;
      await respond(
        sibling.call_id,
        () => client.toolApprovalResponse(sibling.call_id, "approve", true),
        () => advance(sibling.call_id),
      );
    }
  };

  const approveMcpToolCall = async (approved: boolean, expectedId?: string) => {
    const id = currentMcpApprovalRequestIdRef.current;
    if (!id || !client || (expectedId !== undefined && expectedId !== id))
      return;
    await respond(
      id,
      () =>
        client.mcpApprovalResponse(
          id,
          approved ? "approve" : "reject",
          approved ? undefined : "User rejected MCP tool call",
        ),
      () => {
        if (currentMcpApprovalRequestIdRef.current !== id) return;
        const next = mcpApprovalQueueRef.current.shift() ?? null;
        currentMcpApprovalRequestIdRef.current = next?.request_id ?? null;
        actions.setMcpApproval(next);
      },
    );
  };

  const respondElicitation = async (
    answer: ElicitationAnswer,
    expectedId?: string,
  ) => {
    const id = currentElicitationIdRef.current;
    if (!id || !client || (expectedId !== undefined && expectedId !== id))
      return false;
    return await respond(
      id,
      () =>
        client.elicitationResponse(
          id,
          answer.action,
          answer.value,
          answer.reason,
        ),
      () => {
        if (currentElicitationIdRef.current !== id) return;
        const next = elicitationQueueRef.current.shift() ?? null;
        currentElicitationIdRef.current = next?.elicitation_id ?? null;
        actions.setElicitation(next);
      },
    );
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
