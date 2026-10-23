/**
 * The TUI's transcript rows, projected from the shared conversation session's
 * items (@omniagents/client): the same transcript the desktop and web UI
 * render, drawn in a terminal. A pure map; the session owns every decision
 * about what happened.
 */

import type { MessageItem } from "@omniagents/client/transcript/chat-types";

import type { Message } from "./appStore.js";
import {
  guardianReviewLine,
  workflowReviewLine,
} from "../components/reviewer-model.js";

/** A terminal shows what a user message carried as markers: "[image]",
 * "[file: notes.pdf]". */
function attachmentMarkers(
  attachments: ReadonlyArray<{ type: string; filename?: string }> | undefined,
): string {
  return (attachments ?? [])
    .map((attachment) =>
      attachment.type === "image"
        ? "[image]"
        : attachment.filename
          ? `[file: ${attachment.filename}]`
          : "[file]",
    )
    .join(" ");
}

function projectOne(item: MessageItem): Message | null {
  switch (item.type) {
    case "chat": {
      if (item.role === "user") {
        const markers = attachmentMarkers(item.attachments);
        return {
          role: "user",
          content: markers
            ? item.content
              ? `${markers}\n${item.content}`
              : markers
            : item.content,
        };
      }
      if (item.command) {
        return {
          role: "command",
          command: item.command,
          content: item.content,
        };
      }
      return { role: item.role, content: item.content };
    }
    case "tool": {
      const kind = item.canonical?.content.tool_kind;
      return {
        role: "tool_call",
        content: "",
        toolCall: {
          tool: item.tool_label ?? item.tool,
          kind: typeof kind === "string" ? kind : undefined,
          input: item.input ?? "",
          call_id: item.call_id ?? item.canonical?.item_id ?? "",
          output: item.status === "result" ? (item.output ?? "") : undefined,
          metadata: item.metadata as Record<string, unknown> | undefined,
          running: item.status === "called",
        },
      };
    }
    case "guardian_review": {
      const line = guardianReviewLine({
        callId: item.request_id,
        tool: item.tool,
        reviewer: item.reviewer,
        outcome: item.outcome,
        kind: item.kind,
        serverLabel: item.server_label,
        riskLevel: item.risk_level,
        rationale: item.rationale,
      });
      return {
        role: "review",
        content: line,
        review: {
          kind: "guardian",
          // A denial stopped a tool call; an allow folds into the beat.
          blocking: item.outcome === "deny",
          line,
          rationale: item.rationale,
        },
      };
    }
    case "workflow_review": {
      const line = workflowReviewLine({
        taskId: item.task_id,
        subject: item.subject,
        reviewer: item.reviewer,
        outcome: item.outcome,
        rationale: item.rationale,
      });
      return {
        role: "review",
        content: line,
        review: {
          kind: "workflow",
          // Only a rejection stopped the step.
          blocking: item.outcome === "reject",
          line,
          rationale: item.rationale,
        },
      };
    }
    case "artifact":
      // The artifacts drawer shows the content; a megabyte of base64 is not
      // transcript material.
      return { role: "system", content: item.title || "(artifact)" };
    case "run_diff": {
      const files = item.stats.filesChanged;
      return {
        role: "system",
        content: `${files} file${files === 1 ? "" : "s"} edited +${item.stats.additions} −${item.stats.deletions}`,
        runDiff: item,
      };
    }
    case "structured":
      return {
        role: "system",
        content: item.summary ? `${item.title}: ${item.summary}` : item.title,
      };
    case "approval":
      // Pending approvals are the approval dialog, not transcript rows.
      return null;
    case "reasoning":
    case "plan":
      // The plan lives in the task dock; reasoning has no terminal renderer.
      return null;
  }
}

export function projectSessionItems(items: readonly MessageItem[]): Message[] {
  const rows: Message[] = [];
  for (const item of items) {
    const row = projectOne(item);
    if (row) {
      rows.push(row);
    }
  }
  return rows;
}
