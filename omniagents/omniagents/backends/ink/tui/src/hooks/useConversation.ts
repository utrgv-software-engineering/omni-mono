/**
 * The open conversation, owned by the shared conversation session
 * (@omniagents/client): the same session layer the desktop and web UI use,
 * so a conversation loads, sends, queues, recovers and runs slash commands
 * identically in every client. This hook mirrors the session into the TUI
 * store, which the Ink components render, and exposes the user's answers.
 */

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { RPCClient } from "@omniagents/client/rpc/client";
import { respondToApproval } from "@omniagents/client/rpc/approval-response";
import type {
  ElicitationRequest,
  ElicitationResponse,
} from "@omniagents/client/rpc/elicitation";
import { isThinking } from "@omniagents/client/machines/chat-session.machine";
import type { ConversationSession } from "@omniagents/client/session/conversation-session";
import { getSessionRegistry } from "@omniagents/client/session/session-registry";
import { $activityBySession } from "@omniagents/client/transcript/activity-store";
import { negotiatedPlanTasks } from "@omniagents/client/transcript/canonical-plan-tasks";
import type {
  ApprovalItem,
  ArtifactItem,
  MessageItem,
  RunDiffItem,
} from "@omniagents/client/transcript/chat-types";

import { useActions, useAppStore } from "../store/appStore.js";
import type { ElicitationInfo, Message } from "../store/appStore.js";
import { projectSessionItems } from "../store/session-items.js";
import { reviseArtifacts } from "../components/artifact-model.js";
import type { ElicitationAnswer } from "../components/ElicitationDialog.js";

export interface UseConversationOptions {
  client: RPCClient | null;
  sessionId: string;
  rpcReady: boolean;
}

function flattenPhase(value: unknown) {
  return typeof value === "string" ? value : (value as { ready: string }).ready;
}

function elicitationInfo(request: ElicitationRequest): ElicitationInfo {
  return {
    elicitation_id: request.elicitationId,
    kind: request.kind,
    message: request.message,
    title: request.title,
    input_schema: request.kind === "form" ? request.inputSchema : undefined,
    options: request.kind === "select" ? request.options : undefined,
    url: request.kind === "url" ? request.url : undefined,
    session_id: request.sessionId,
    run_id: request.runId,
    expires_at: request.expiresAt,
  };
}

function elicitationResponse(answer: ElicitationAnswer): ElicitationResponse {
  if (answer.action === "accept") {
    return { action: "accept", value: answer.value };
  }
  if (answer.action === "decline") {
    return { action: "decline", reason: answer.reason };
  }
  return { action: "cancel", reason: answer.reason };
}

export function useConversation({
  client,
  sessionId,
  rpcReady,
}: UseConversationOptions) {
  const actions = useActions();
  const registry = useMemo(
    () => (client ? getSessionRegistry(client) : null),
    [client],
  );
  const session = useMemo(
    () => (registry ? registry.get(sessionId) : null),
    [registry, sessionId],
  );
  // Where a recap landed in the transcript: it is retrospective prose, so it
  // stays where it arrived rather than floating to the end.
  const recapAt = useRef<{ timestamp: number; index: number } | null>(null);
  // The session's own status line, last applied. A local error (a refused
  // send) stays on screen until the session's status actually changes.
  const sessionStatus = useRef<string | null | undefined>(undefined);

  useEffect(
    () => (registry ? registry.retainSession(sessionId) : undefined),
    [registry, sessionId],
  );

  useEffect(() => {
    if (!session || !rpcReady) return;
    // The side panels (goal, wakeup, loops, queue, workers) are read once the
    // conversation has loaded; live updates keep them current after that.
    session
      .load()
      .then(() => session.refreshPanels())
      .catch((error: unknown) => {
        actions.setStatus(
          `Could not load the conversation: ${error instanceof Error ? error.message : String(error)}`,
          "error",
          false,
        );
      });
  }, [session, rpcReady, actions]);

  useEffect(() => {
    if (!session || !client) return;
    recapAt.current = null;
    sessionStatus.current = undefined;
    const sync = () =>
      syncSessionToStore(session, client, recapAt, sessionStatus);
    sync();
    const actor = session.actor.subscribe(sync);
    const offPanels = session.panels.state.listen(sync);
    const offActivity = $activityBySession.listen(sync);
    const offElicitations = client.elicitations.onChange(sync);
    return () => {
      actor.unsubscribe();
      offPanels();
      offActivity();
      offElicitations();
    };
  }, [session, client]);

  const pendingApproval = (kind: "function" | "mcp", expectedId?: string) => {
    const pending = session
      ? [...session.actor.getSnapshot().context.pendingApprovals.values()]
      : [];
    const head = pending.find(
      (approval) => (approval.kind ?? "function") === kind,
    );
    return head && (expectedId === undefined || head.request_id === expectedId)
      ? head
      : null;
  };

  const decide = async (
    approval: ApprovalItem,
    value: "yes" | "always" | "no",
  ) => {
    if (!client || !session) return false;
    try {
      await respondToApproval(
        client,
        approval.request_id,
        value,
        approval.kind ?? "function",
      );
      session.receive({
        type: "APPROVAL_DECIDED",
        request_id: approval.request_id,
        value,
      });
      return true;
    } catch (error) {
      actions.setStatus(
        `Could not send the answer. Please retry: ${String(error)}`,
        "error",
        false,
      );
      return false;
    }
  };

  const approveToolCall = async (
    approved: boolean,
    alwaysApprove: boolean,
    expectedId?: string,
  ) => {
    const approval = pendingApproval("function", expectedId);
    if (!approval) return;
    const value = !approved ? "no" : alwaysApprove ? "always" : "yes";
    if (!(await decide(approval, value)) || value !== "always") return;
    // "Always" covers the same tool's other pending calls.
    for (const sibling of session
      ? [...session.actor.getSnapshot().context.pendingApprovals.values()]
      : []) {
      if (
        (sibling.kind ?? "function") === "function" &&
        sibling.tool === approval.tool
      ) {
        await decide(sibling, "always");
      }
    }
  };

  const approveMcpToolCall = async (approved: boolean, expectedId?: string) => {
    const approval = pendingApproval("mcp", expectedId);
    if (approval) await decide(approval, approved ? "yes" : "no");
  };

  const respondElicitation = async (
    answer: ElicitationAnswer,
    expectedId?: string,
  ): Promise<boolean> => {
    if (!client) return false;
    const current = useAppStore.getState().elicitation;
    if (
      !current ||
      (expectedId !== undefined && expectedId !== current.elicitation_id)
    ) {
      return false;
    }
    try {
      await client.elicitations.respond(
        current.elicitation_id,
        elicitationResponse(answer),
      );
      return true;
    } catch (error) {
      actions.setStatus(
        `Could not send the answer. Please retry: ${String(error)}`,
        "error",
        false,
      );
      return false;
    }
  };

  const cancelRun = useCallback(async () => {
    if (!session) return;
    try {
      await session.stopRun();
    } catch (error) {
      actions.setStatus(
        `Could not stop the run: ${error instanceof Error ? error.message : String(error)}`,
        "error",
        false,
      );
    }
  }, [session, actions]);

  /** Send a message or a slash command, exactly as every other client does.
   * Resolves false when it did not go out; the caller keeps the text. */
  const send = useCallback(
    async (text: string): Promise<boolean> => {
      if (!session) return false;
      try {
        await session.send(text);
        return true;
      } catch (error) {
        actions.setStatus(
          error instanceof Error ? error.message : String(error),
          "error",
          false,
        );
        return false;
      }
    },
    [session, actions],
  );

  /** Dismiss one notification, or every one when `id` is omitted. */
  const dismissNotification = useCallback(
    (id?: string) =>
      session?.panels.set("notifications", (previous) =>
        id === undefined
          ? []
          : previous.filter((notification) => notification.id !== id),
      ),
    [session],
  );

  return {
    session,
    send,
    approveToolCall,
    approveMcpToolCall,
    respondElicitation,
    cancelRun,
    dismissNotification,
  };
}

/** Everything the TUI shows about the conversation, from the session. */
function syncSessionToStore(
  session: ConversationSession,
  client: RPCClient,
  recapAt: { current: { timestamp: number; index: number } | null },
  sessionStatus: { current: string | null | undefined },
) {
  const { actions, elicitation: shownElicitation } = useAppStore.getState();
  const snapshot = session.actor.getSnapshot();
  const context = snapshot.context;
  const panels = session.panels.state.get();
  const items = context.items as MessageItem[];

  const messages: Message[] = projectSessionItems(items);
  if (panels.recap) {
    if (recapAt.current?.timestamp !== panels.recap.timestamp) {
      recapAt.current = {
        timestamp: panels.recap.timestamp,
        index: messages.length,
      };
    }
    messages.splice(Math.min(recapAt.current.index, messages.length), 0, {
      role: "assistant",
      content: `**Session recap**\n\n${panels.recap.text}`,
    });
  }
  actions.setMessages(messages);

  const phase = flattenPhase(snapshot.value);
  actions.setCurrentRunId(context.runId ?? null);
  const status = context.status ?? null;
  if (status !== sessionStatus.current) {
    sessionStatus.current = status;
    actions.setStatus(
      status,
      status ? "progress" : null,
      context.statusSpinner,
    );
  }
  actions.setThinking(isThinking(phase as Parameters<typeof isThinking>[0]));

  const pending = [...context.pendingApprovals.values()];
  const tool = pending.find(
    (approval) => (approval.kind ?? "function") === "function",
  );
  const mcp = pending.find((approval) => approval.kind === "mcp");
  actions.setApproval(
    tool
      ? {
          call_id: tool.request_id,
          tool: tool.tool_label ?? tool.tool,
          arguments: tool.argumentsText ?? "{}",
          metadata: tool.metadata as Record<string, unknown> | undefined,
          session_id: tool.session_id,
          run_id: tool.run_id,
        }
      : null,
  );
  actions.setMcpApproval(
    mcp
      ? {
          request_id: mcp.request_id,
          server_label: mcp.server_label ?? "",
          tool: mcp.tool_label ?? mcp.tool,
          arguments: mcp.argumentsText ?? "{}",
          session_id: mcp.session_id,
          run_id: mcp.run_id,
        }
      : null,
  );

  const elicitation = client.elicitations
    .list()
    .find(
      (request) =>
        request.sessionId === undefined || request.sessionId === session.id,
    );
  if (
    (elicitation?.elicitationId ?? null) !==
    (shownElicitation?.elicitation_id ?? null)
  ) {
    actions.setElicitation(elicitation ? elicitationInfo(elicitation) : null);
  }

  actions.setEscalation(panels.escalation);
  actions.setQueuedMessages(panels.queuedMessages);
  actions.setGoal(panels.goalSnapshot);
  actions.setWakeup(panels.wakeupSnapshot);
  actions.setLoops(panels.loopTasks);
  actions.setNotifications(panels.notifications);
  if (panels.usage) actions.setUsage(panels.usage);
  const model = panels.models.find((entry) => entry.id === panels.activeModel);
  actions.setSessionState({
    model: panels.activeModel,
    label: model?.label ?? null,
    provider: model?.provider.name ?? null,
    reasoning_effort: panels.reasoningEffort,
  });

  const activity = $activityBySession.get()[session.id];
  actions.setSubagents(activity?.subagents ?? []);
  actions.setBashJobs(panels.liveBashJobs ?? activity?.jobs ?? []);
  actions.setTasks(
    negotiatedPlanTasks(
      items,
      [],
      client.supportsExperimentalFeature("plansAndDiffs"),
    ),
  );
  actions.setArtifacts(
    reviseArtifacts(
      useAppStore.getState().artifacts,
      items
        .filter((item): item is ArtifactItem => item.type === "artifact")
        .map((item) => ({
          artifactId: item.artifact_id,
          title: item.title,
          content: item.content,
          mode: item.mode ?? "markdown",
          sessionId: item.session_id ?? session.id,
          updatedAt: item.updated_at ?? 0,
        })),
    ),
  );
  const runDiffs = items.filter(
    (item): item is RunDiffItem => item.type === "run_diff",
  );
  actions.setRunDiff(runDiffs[runDiffs.length - 1] ?? null);
}
