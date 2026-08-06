/**
 * State normalization for the canonical conversation domain objects.
 *
 * The TUI's transcript was previously folded out of the live notification
 * stream, so a reconnect or a cold `get_session_history` produced a
 * different shape than a live run did. `normalizeItems` projects the
 * server's already-resolved items onto the store's `Message` model: a pure
 * map, no event folding, no "still running?" inference — the item's status
 * is the answer.
 */

import type {
  ConversationItem,
  ConversationToolKind,
} from "../types/conversation.js";
import type { Message, ToolCallInfo } from "./appStore.js";

const TERMINAL_STATUSES = ["completed", "failed", "cancelled"] as const;

export function isTerminal(item: ConversationItem): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(item.status);
}

export function toolKind(item: ConversationItem): ConversationToolKind {
  const kind = item.content.tool_kind;
  return typeof kind === "string" ? (kind as ConversationToolKind) : "other";
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asText(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function toolCallInfo(item: ConversationItem): ToolCallInfo {
  return {
    tool: text(item.content.tool) || "tool",
    kind: toolKind(item),
    input: asText(item.content.input),
    call_id:
      typeof item.content.call_id === "string"
        ? item.content.call_id
        : item.item_id,
    output: isTerminal(item) ? asText(item.content.output) : undefined,
    metadata: record(item.content.metadata),
    // Non-terminal means genuinely in flight. Terminal means the
    // authoritative final state is recorded — including a tool the server
    // finalized at run_end because its result never arrived.
    running: !isTerminal(item),
  };
}

function normalizeOne(item: ConversationItem): Message | null {
  switch (item.kind) {
    case "user_message":
      return { role: "user", content: text(item.content.text) };
    case "agent_message":
      return {
        role: item.role === "system" ? "system" : "assistant",
        content: text(item.content.text),
      };
    case "compaction":
      return { role: "system", content: text(item.content.summary) };
    case "tool_call":
      return {
        role: "tool_call",
        content: "",
        toolCall: toolCallInfo(item),
      };
    case "approval":
      if (isTerminal(item)) {
        // Already resolved: render the decision, not a fresh prompt.
        const decision =
          typeof item.content.decision === "string"
            ? item.content.decision
            : item.status;
        return {
          role: "system",
          content: `approval ${decision}: ${text(item.content.tool) || "tool"}`,
        };
      }
      return {
        role: "approval_request",
        content: "",
        approval: {
          call_id:
            typeof item.content.call_id === "string"
              ? item.content.call_id
              : typeof item.content.request_id === "string"
                ? item.content.request_id
                : item.item_id,
          tool: text(item.content.tool) || "tool",
          arguments: asText(item.content.arguments),
          metadata: record(item.content.metadata),
          session_id: item.thread_id,
        },
      };
    case "artifact":
      return {
        role: "system",
        content: text(item.content.title) || "(artifact)",
      };
    case "reasoning":
    case "elicitation":
      // No transcript renderer in this client. Not data loss: the items
      // stay in the store and `listItems({ kinds })` returns them.
      return null;
    default:
      return null;
  }
}

/**
 * Project canonical items onto the store's `Message[]`. Sorts by `seq`
 * defensively so a caller that merged pages out of order still renders in
 * the server's authoritative order.
 */
export function normalizeItems(items: ConversationItem[]): Message[] {
  const ordered = [...items].sort((a, b) => a.seq - b.seq);
  const out: Message[] = [];
  for (const item of ordered) {
    const normalized = normalizeOne(item);
    if (normalized) out.push(normalized);
  }
  return out;
}

/**
 * Merge an incoming page into a cached list keyed on canonical `item_id`.
 * `revision` breaks ties, so out-of-order page arrival cannot regress an
 * item that already advanced.
 */
export function mergeItems(
  cached: ConversationItem[],
  incoming: ConversationItem[],
): ConversationItem[] {
  const byId = new Map<string, ConversationItem>();
  for (const item of cached) byId.set(item.item_id, item);
  for (const item of incoming) {
    const existing = byId.get(item.item_id);
    if (existing && existing.revision > item.revision) continue;
    byId.set(item.item_id, item);
  }
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}
