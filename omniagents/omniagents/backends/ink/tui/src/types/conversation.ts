/**
 * Canonical conversation domain objects (tkt_a-QF2Oc4OAsD).
 *
 * Mirrors the server's `omniagents/core/conversation/model.py` wire shapes.
 * `content` stays an open record on purpose: its fields follow the item
 * kind and grow with the tools, so pinning a closed schema here would turn
 * every tool change into a protocol break.
 */

export type ConversationItemKind =
  | "user_message"
  | "agent_message"
  | "reasoning"
  | "tool_call"
  | "approval"
  | "elicitation"
  | "artifact"
  | "compaction"
  /** Reserved, not yet produced — see protocol.md § Conversation Domain Objects. */
  | "plan"
  | "run_diff";

/** Item lifecycle. Exactly one terminal state is ever reached. */
export type ConversationItemStatus =
  | "started"
  | "completed"
  | "failed"
  | "cancelled";

export type ConversationTurnStatus =
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ConversationToolKind =
  | "command"
  | "file"
  | "search"
  | "web"
  | "mcp"
  | "plan"
  | "artifact"
  | "worker"
  | "other";

/** `recorded` = written live by the runtime; `adapter` = projected from
 * legacy history by the compatibility adapter. */
export type ConversationSource = "recorded" | "adapter";

export interface ConversationThread {
  thread_id: string;
  user_id: string | null;
  status: "active" | "archived";
  title: string | null;
  created_at: number;
  updated_at: number;
  last_seq: number;
  turn_count: number;
  item_count: number;
  parent_thread_id: string | null;
  branched_from_item_id: string | null;
  usage: Record<string, unknown>;
  compaction: Record<string, unknown>;
  source: ConversationSource;
  /** When pre-existing history was materialized; null = not yet settled. */
  projected_at: number | null;
  schema_version: number;
}

export interface ConversationTurn {
  turn_id: string;
  thread_id: string;
  ordinal: number;
  status: ConversationTurnStatus;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  prompt: string | null;
  prompt_role: string | null;
  end_reason: string | null;
  error: Record<string, unknown> | null;
  usage: Record<string, unknown>;
  model: string | null;
  model_ref: string | null;
  item_count: number;
  first_seq: number | null;
  last_seq: number | null;
  attempts: number;
  source: ConversationSource;
  schema_version: number;
}

export interface ConversationItem {
  item_id: string;
  thread_id: string;
  turn_id: string | null;
  /** Thread-monotonic ordinal. The ONLY ordering clients should rely on. */
  seq: number;
  kind: ConversationItemKind;
  status: ConversationItemStatus;
  role: string | null;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  revision: number;
  content: Record<string, unknown>;
  source_ref: Record<string, unknown>;
  /** Exempt from run-end finalization; the owning subsystem lands it. */
  long_lived: boolean;
  source: ConversationSource;
  schema_version: number;
}

export interface ConversationTurnPage {
  thread_id: string;
  turns: ConversationTurn[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
}

export interface ConversationItemPage {
  thread_id: string;
  turn_id: string | null;
  items: ConversationItem[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
}
